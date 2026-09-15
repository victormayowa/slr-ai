"""Repository deposits: Zenodo (DOI, versions), Figshare, GitHub, GitLab, and OSF.

Access tokens are used for the request only; they're never stored or logged. Publishing on Zenodo or Figshare is
permanent, so deposits stay as drafts unless the caller explicitly asks to publish.
"""

import base64
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 120
ZENODO_API = {False: "https://zenodo.org/api", True: "https://sandbox.zenodo.org/api"}
FIGSHARE_API = "https://api.figshare.com/v2"
GITHUB_API = "https://api.github.com"
DEFAULT_GITLAB_API = "https://gitlab.com/api/v4"


class RepositoryError(Exception):
    """A repository request failed. The message is safe to show users."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class DepositResult:
    external_id: str
    url: str
    status: str = "draft"
    doi: str = ""
    concept_id: str = ""
    details: dict[str, Any] = field(default_factory=dict)


def _request(label: str, method: str, url: str, **kwargs: Any) -> requests.Response:
    try:
        response = requests.request(method, url, timeout=TIMEOUT_SECONDS, **kwargs)
    except requests.RequestException as exc:
        logger.warning("%s request failed: %s", label, type(exc).__name__)
        raise RepositoryError(f"{label} couldn't be reached. Try again shortly.") from exc
    if response.status_code in (401, 403):
        raise RepositoryError(f"{label} rejected the access token, or it lacks the needed permissions", 400)
    if not response.ok:
        raise RepositoryError(f"{label} returned an error ({response.status_code})")
    return response


def zenodo_metadata(
    title: str,
    description: str,
    creators: list[dict[str, str]],
    keywords: list[str],
    version: str,
    related_doi: str = "",
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "upload_type": "dataset",
        "title": title,
        "description": description,
        "creators": creators or [{"name": "OmniReview review team"}],
        "keywords": keywords,
        "version": version,
        "access_right": "open",
        "license": "cc-by-4.0",
    }
    if related_doi:
        metadata["related_identifiers"] = [{"identifier": related_doi, "relation": "isSupplementTo", "scheme": "doi"}]
    return metadata


def zenodo_deposit(
    token: str,
    sandbox: bool,
    metadata: dict[str, Any],
    files: dict[str, bytes],
    publish: bool,
    previous_deposition_id: str = "",
) -> DepositResult:
    base = ZENODO_API[sandbox]
    headers = {"Authorization": f"Bearer {token}"}
    if previous_deposition_id:
        created = _request(
            "Zenodo", "POST", f"{base}/deposit/depositions/{previous_deposition_id}/actions/newversion", headers=headers
        ).json()
        draft = _request("Zenodo", "GET", created["links"]["latest_draft"], headers=headers).json()
        for old in draft.get("files", []):
            _request("Zenodo", "DELETE", f"{base}/deposit/depositions/{draft['id']}/files/{old['id']}", headers=headers)
    else:
        draft = _request("Zenodo", "POST", f"{base}/deposit/depositions", headers=headers, json={}).json()
    deposition_id = str(draft["id"])
    bucket = draft["links"]["bucket"]
    for name, content in files.items():
        _request("Zenodo", "PUT", f"{bucket}/{quote(name)}", headers=headers, data=content)
    updated = _request(
        "Zenodo", "PUT", f"{base}/deposit/depositions/{deposition_id}", headers=headers, json={"metadata": metadata}
    ).json()
    result = DepositResult(
        deposition_id,
        (updated.get("links") or {}).get("html", ""),
        "draft",
        (updated.get("metadata") or {}).get("prereserve_doi", {}).get("doi", ""),
        str(updated.get("conceptrecid", "")),
    )
    if publish:
        published = _request(
            "Zenodo", "POST", f"{base}/deposit/depositions/{deposition_id}/actions/publish", headers=headers
        ).json()
        result.status = "published"
        result.doi = published.get("doi", result.doi)
        result.url = (published.get("links") or {}).get("record_html") or (published.get("links") or {}).get(
            "html", result.url
        )
        result.concept_id = str(published.get("conceptrecid", result.concept_id))
    return result


def figshare_deposit(token: str, metadata: dict[str, Any], files: dict[str, bytes], publish: bool) -> DepositResult:
    headers = {"Authorization": f"token {token}"}
    body = {
        "title": metadata["title"],
        "description": metadata.get("description", ""),
        "defined_type": "dataset",
        "keywords": metadata.get("keywords", []),
        "authors": [{"name": c["name"]} for c in metadata.get("creators", [])],
    }
    location = _request("Figshare", "POST", f"{FIGSHARE_API}/account/articles", headers=headers, json=body).json()[
        "location"
    ]
    article = _request("Figshare", "GET", location, headers=headers).json()
    article_id = article["id"]
    for name, content in files.items():
        info = {"name": name, "size": len(content), "md5": hashlib.md5(content).hexdigest()}  # noqa: S324 (Figshare's API needs MD5)
        file_location = _request(
            "Figshare", "POST", f"{FIGSHARE_API}/account/articles/{article_id}/files", headers=headers, json=info
        ).json()["location"]
        file_info = _request("Figshare", "GET", file_location, headers=headers).json()
        upload = _request("Figshare", "GET", file_info["upload_url"], headers=headers).json()
        for part in upload.get("parts", []):
            chunk = content[part["startOffset"] : part["endOffset"] + 1]
            _request("Figshare", "PUT", f"{file_info['upload_url']}/{part['partNo']}", headers=headers, data=chunk)
        _request(
            "Figshare", "POST", f"{FIGSHARE_API}/account/articles/{article_id}/files/{file_info['id']}", headers=headers
        )
    result = DepositResult(str(article_id), article.get("url_private_html", ""), "draft")
    if publish:
        _request("Figshare", "POST", f"{FIGSHARE_API}/account/articles/{article_id}/publish", headers=headers)
        public = _request("Figshare", "GET", f"{FIGSHARE_API}/account/articles/{article_id}", headers=headers).json()
        result.status, result.doi, result.url = (
            "published",
            public.get("doi", ""),
            public.get("url_public_html", result.url),
        )
    return result


def github_deposit(token: str, repository: str, description: str, files: dict[str, bytes]) -> DepositResult:
    """Commit the files to a repository: an existing "owner/name", or a new private repository named `repository`."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    if "/" in repository:
        repo = _request("GitHub", "GET", f"{GITHUB_API}/repos/{repository}", headers=headers).json()
    else:
        repo = _request(
            "GitHub",
            "POST",
            f"{GITHUB_API}/user/repos",
            headers=headers,
            json={"name": repository, "private": True, "description": description[:350], "auto_init": True},
        ).json()
    full_name = repo["full_name"]
    for path, content in files.items():
        existing = requests.get(
            f"{GITHUB_API}/repos/{full_name}/contents/{quote(path)}", headers=headers, timeout=TIMEOUT_SECONDS
        )
        body: dict[str, Any] = {"message": f"Add {path} from OmniReview", "content": base64.b64encode(content).decode()}
        if existing.ok:
            body["sha"] = existing.json().get("sha")
            body["message"] = f"Update {path} from OmniReview"
        _request("GitHub", "PUT", f"{GITHUB_API}/repos/{full_name}/contents/{quote(path)}", headers=headers, json=body)
    return DepositResult(full_name, repo.get("html_url", ""), "published")


def gitlab_deposit(
    token: str, repository: str, description: str, files: dict[str, bytes], api_url: str = ""
) -> DepositResult:
    """Commit the files to a GitLab project: an existing "group/name" path, or a new private project."""
    base = (api_url or DEFAULT_GITLAB_API).rstrip("/")
    headers = {"PRIVATE-TOKEN": token}
    if "/" in repository:
        project = _request("GitLab", "GET", f"{base}/projects/{quote(repository, safe='')}", headers=headers).json()
    else:
        project = _request(
            "GitLab",
            "POST",
            f"{base}/projects",
            headers=headers,
            json={
                "name": repository,
                "visibility": "private",
                "description": description[:2000],
                "initialize_with_readme": True,
            },
        ).json()
    project_id = project["id"]
    branch = project.get("default_branch") or "main"
    tree = requests.get(
        f"{base}/projects/{project_id}/repository/tree",
        params={"recursive": "true", "per_page": "100"},
        headers=headers,
        timeout=TIMEOUT_SECONDS,
    )
    existing = {item["path"] for item in tree.json()} if tree.ok else set()
    actions = [
        {
            "action": "update" if path in existing else "create",
            "file_path": path,
            "content": base64.b64encode(content).decode(),
            "encoding": "base64",
        }
        for path, content in files.items()
    ]
    _request(
        "GitLab",
        "POST",
        f"{base}/projects/{project_id}/repository/commits",
        headers=headers,
        json={"branch": branch, "commit_message": "Add review materials from OmniReview", "actions": actions},
    )
    return DepositResult(str(project_id), project.get("web_url", ""), "published")


def osf_deposit(token: str, title: str, description: str, files: dict[str, bytes]) -> DepositResult:
    """A new private OSF project holding the files."""
    headers = {"Authorization": f"Bearer {token}"}
    body = {
        "data": {
            "type": "nodes",
            "attributes": {
                "title": title[:200],
                "category": "data",
                "description": description[:1000],
                "public": False,
            },
        }
    }
    node = _request(
        "OSF",
        "POST",
        "https://api.osf.io/v2/nodes/",
        headers={**headers, "Content-Type": "application/vnd.api+json"},
        json=body,
    ).json()["data"]
    node_id = node["id"]
    for name, content in files.items():
        _request(
            "OSF",
            "PUT",
            f"https://files.osf.io/v1/resources/{node_id}/providers/osfstorage/",
            params={"kind": "file", "name": name.replace("/", "_")},
            data=content,
            headers=headers,
        )
    return DepositResult(node_id, (node.get("links") or {}).get("html") or f"https://osf.io/{node_id}/", "draft")
