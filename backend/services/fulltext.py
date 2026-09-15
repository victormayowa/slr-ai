"""Find open-access full texts: Europe PMC's JATS XML first, then an open-access PDF located by Unpaywall.

Every attempt is reported, found or not, so a project keeps a record of how each report was sought (PRISMA 2020
"reports sought for retrieval"). Links from Unpaywall point to other people's servers, so a file is downloaded only from
public internet addresses, over http or https on the standard ports, with a size cap and a limited number of redirects.
Each redirect is checked again. Links behind institutional access (EZproxy) are opened in the user's browser instead.
"""

import ipaddress
import logging
import re
import socket
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import quote, urljoin, urlsplit

import requests

from services.record_import import normalize_doi

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 30
MAX_REDIRECTS = 5
EUROPEPMC_REST = "https://www.ebi.ac.uk/europepmc/webservices/rest"
UNPAYWALL_URL = "https://api.unpaywall.org/v2/"
# Publisher PDFs tried for one record, from Unpaywall's best location onwards.
MAX_PDF_LOCATIONS = 3
USER_AGENT = "OmniReview/1.0 (systematic review full-text retrieval)"

Outcome = Literal["found", "not_found", "skipped", "error"]


class FetchError(Exception):
    """A request or download failed. The message is safe to show users."""


@dataclass
class Attempt:
    # "europepmc" or "unpaywall"
    source: str
    outcome: Outcome
    detail: str


@dataclass
class FetchedFile:
    content: bytes
    file_name: str
    # "europepmc" or "unpaywall"
    origin: str
    source_url: str
    license: str = ""
    oa_status: str = ""
    version: str = ""


@dataclass
class RetrievalResult:
    file: FetchedFile | None
    attempts: list[Attempt] = field(default_factory=list)


@dataclass(frozen=True)
class RecordIds:
    doi: str = ""
    pmid: str = ""
    pmcid: str = ""


def normalize_pmcid(value: str) -> str:
    value = value.strip().upper()
    if value.isdigit():
        value = f"PMC{value}"
    return value if re.fullmatch(r"PMC\d+", value) else ""


def record_ids(doi: str, identifiers: dict[str, str] | None) -> RecordIds:
    identifiers = identifiers or {}
    pmid = str(identifiers.get("pmid") or "").strip()
    return RecordIds(
        doi=normalize_doi(doi or ""),
        pmid=pmid if pmid.isdigit() else "",
        pmcid=normalize_pmcid(str(identifiers.get("pmcid") or "")),
    )


def _get(url: str, params: dict[str, str] | None = None) -> requests.Response:
    try:
        return requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        logger.warning("Full-text request to %s failed", urlsplit(url).hostname, exc_info=True)
        raise FetchError("The request failed. Try again shortly.") from exc


def europepmc_pmcid(ids: RecordIds) -> str:
    """The record's PubMed Central ID, looked up in Europe PMC by DOI or PMID when the record doesn't have one."""
    if ids.pmcid:
        return ids.pmcid
    if ids.doi:
        query = f'DOI:"{ids.doi.replace(chr(34), "")}"'
    elif ids.pmid:
        query = f"EXT_ID:{ids.pmid} AND SRC:MED"
    else:
        return ""
    response = _get(
        f"{EUROPEPMC_REST}/search", {"query": query, "format": "json", "resultType": "lite", "pageSize": "1"}
    )
    if response.status_code != 200:
        raise FetchError(f"Europe PMC answered with HTTP {response.status_code}")
    try:
        results = (response.json().get("resultList") or {}).get("result") or []
    except ValueError as exc:
        raise FetchError("Europe PMC returned an unreadable answer") from exc
    return normalize_pmcid(str((results[0] if results else {}).get("pmcid") or ""))


def europepmc_full_text_xml(pmcid: str, max_bytes: int) -> bytes | None:
    """The article's JATS XML, or None when Europe PMC has no open-access full text for it."""
    response = _get(f"{EUROPEPMC_REST}/{pmcid}/fullTextXML")
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise FetchError(f"Europe PMC answered with HTTP {response.status_code}")
    if len(response.content) > max_bytes:
        raise FetchError("The full text is larger than the size limit")
    return response.content or None


def unpaywall_lookup(doi: str, email: str) -> dict | None:
    response = _get(f"{UNPAYWALL_URL}{quote(doi, safe='/')}", {"email": email})
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise FetchError(f"Unpaywall answered with HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError as exc:
        raise FetchError("Unpaywall returned an unreadable answer") from exc
    return data if isinstance(data, dict) else None


def check_public_url(url: str) -> None:
    """Refuse links that aren't http(s) on a standard port, or whose host resolves to a non-public address."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FetchError("Only http and https links are downloaded")
    if parts.username or parts.password:
        raise FetchError("Links with credentials aren't downloaded")
    try:
        port = parts.port
    except ValueError as exc:
        raise FetchError("The link has an invalid port") from exc
    if port not in (None, 80, 443):
        raise FetchError("Links to non-standard ports aren't downloaded")
    try:
        addresses = socket.getaddrinfo(parts.hostname, port or (443 if parts.scheme == "https" else 80))
    except (socket.gaierror, UnicodeError) as exc:
        raise FetchError("The link's server couldn't be found") from exc
    for address in addresses:
        ip = ipaddress.ip_address(str(address[4][0]).split("%")[0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if not ip.is_global:
            raise FetchError("Links to private or local network addresses aren't downloaded")


def download_public_file(url: str, max_bytes: int) -> tuple[bytes, str]:
    """Download a file from a public address. Returns the content and the final URL after redirects."""
    for _ in range(MAX_REDIRECTS + 1):
        check_public_url(url)
        try:
            response = requests.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT_SECONDS,
                stream=True,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise FetchError("The download failed") from exc
        with response:
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                if not location:
                    raise FetchError("The server redirected without a location")
                url = urljoin(url, location)
                continue
            if response.status_code != 200:
                raise FetchError(f"The server answered with HTTP {response.status_code}")
            declared = response.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > max_bytes:
                raise FetchError("The file is larger than the size limit")
            chunks, size = [], 0
            try:
                for chunk in response.iter_content(64 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise FetchError("The file is larger than the size limit")
                    chunks.append(chunk)
            except requests.RequestException as exc:
                raise FetchError("The download was interrupted") from exc
            return b"".join(chunks), url
    raise FetchError("The server redirected too many times")


def jats_license(content: bytes) -> str:
    """A short licence name, such as "cc-by", from the licence link in JATS XML; empty when there isn't one."""
    head = content[:200_000]
    if match := re.search(rb"creativecommons\.org/licenses/([a-z-]+)/", head):
        return f"cc-{match.group(1).decode()}"
    if re.search(rb"creativecommons\.org/publicdomain/zero", head):
        return "cc0"
    return ""


def _europepmc(ids: RecordIds, max_bytes: int, attempts: list[Attempt]) -> FetchedFile | None:
    if not (ids.doi or ids.pmid or ids.pmcid):
        attempts.append(Attempt("europepmc", "skipped", "The record has no DOI, PMID, or PMCID"))
        return None
    try:
        pmcid = europepmc_pmcid(ids)
        if not pmcid:
            attempts.append(Attempt("europepmc", "not_found", "Not in PubMed Central"))
            return None
        xml = europepmc_full_text_xml(pmcid, max_bytes)
    except FetchError as exc:
        attempts.append(Attempt("europepmc", "error", str(exc)))
        return None
    if xml is None:
        attempts.append(Attempt("europepmc", "not_found", f"{pmcid} has no open-access full text in Europe PMC"))
        return None
    attempts.append(Attempt("europepmc", "found", f"JATS XML full text of {pmcid}"))
    return FetchedFile(
        content=xml,
        file_name=f"{pmcid}.xml",
        origin="europepmc",
        source_url=f"{EUROPEPMC_REST}/{pmcid}/fullTextXML",
        license=jats_license(xml),
    )


def _unpaywall(ids: RecordIds, email: str, max_bytes: int, attempts: list[Attempt]) -> FetchedFile | None:
    if not ids.doi:
        attempts.append(Attempt("unpaywall", "skipped", "Unpaywall needs a DOI"))
        return None
    if not email:
        attempts.append(Attempt("unpaywall", "skipped", "Set UNPAYWALL_EMAIL on the server to use Unpaywall"))
        return None
    try:
        data = unpaywall_lookup(ids.doi, email)
    except FetchError as exc:
        attempts.append(Attempt("unpaywall", "error", str(exc)))
        return None
    if not data or not data.get("is_oa"):
        attempts.append(Attempt("unpaywall", "not_found", "Unpaywall knows no open-access copy"))
        return None

    locations: list[dict] = []
    for location in [data.get("best_oa_location"), *(data.get("oa_locations") or [])]:
        if isinstance(location, dict) and location.get("url_for_pdf"):
            if all(location["url_for_pdf"] != known["url_for_pdf"] for known in locations):
                locations.append(location)
    if not locations:
        attempts.append(Attempt("unpaywall", "not_found", "The open-access copies are web pages without a PDF link"))
        return None

    for location in locations[:MAX_PDF_LOCATIONS]:
        pdf_url = str(location["url_for_pdf"])
        host = urlsplit(pdf_url).hostname or "the server"
        try:
            content, final_url = download_public_file(pdf_url, max_bytes)
        except FetchError as exc:
            attempts.append(Attempt("unpaywall", "error", f"{host}: {exc}"))
            continue
        if b"%PDF-" not in content[:1024]:
            attempts.append(
                Attempt(
                    "unpaywall", "not_found", f"{host} returned a web page instead of a PDF (it may need a browser)"
                )
            )
            continue
        attempts.append(Attempt("unpaywall", "found", f"Open-access PDF from {host}"))
        return FetchedFile(
            content=content,
            file_name=f"{ids.doi.replace('/', '_')}.pdf"[:200],
            origin="unpaywall",
            source_url=final_url,
            license=str(location.get("license") or ""),
            oa_status=str(data.get("oa_status") or ""),
            version=str(location.get("version") or ""),
        )
    return None


def find_full_text(ids: RecordIds, unpaywall_email: str, max_bytes: int) -> RetrievalResult:
    """Try each source in turn and stop at the first full text found."""
    attempts: list[Attempt] = []
    found = _europepmc(ids, max_bytes, attempts) or _unpaywall(ids, unpaywall_email, max_bytes, attempts)
    return RetrievalResult(found, attempts)
