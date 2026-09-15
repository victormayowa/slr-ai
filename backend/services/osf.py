"""OSF (Open Science Framework): putting a locked protocol in a new private OSF project, ready for the team to register.

The reviewer's personal access token is used for this request only; it's never stored or logged. OmniReview doesn't
create the formal registration: OSF registrations are public and permanent, so reviewers complete that step on OSF.
"""

import json
import logging
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

OSF_API_URL = "https://api.osf.io/v2"
OSF_FILES_URL = "https://files.osf.io/v1"
REQUEST_TIMEOUT_SECONDS = 60


class OSFError(Exception):
    """An OSF request failed. The message is safe to show users."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class OSFDeposit:
    node_id: str
    url: str
    file_name: str


def deposit_protocol(
    token: str, title: str, description: str, file_name: str, content: bytes, content_type: str
) -> OSFDeposit:
    headers = {"Authorization": f"Bearer {token}"}
    body = {
        "data": {
            "type": "nodes",
            "attributes": {
                "title": title[:200],
                "category": "project",
                "description": description[:1000],
                "public": False,
            },
        }
    }
    try:
        created = requests.post(
            f"{OSF_API_URL}/nodes/",
            data=json.dumps(body),
            headers={**headers, "Content-Type": "application/vnd.api+json"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if created.status_code in (401, 403):
            raise OSFError("OSF rejected the personal access token. It needs the osf.full_write scope.", 400)
        created.raise_for_status()
        node = created.json()["data"]
        node_id = node["id"]
        url = (node.get("links") or {}).get("html") or f"https://osf.io/{node_id}/"
        uploaded = requests.put(
            f"{OSF_FILES_URL}/resources/{node_id}/providers/osfstorage/",
            params={"kind": "file", "name": file_name},
            data=content,
            headers={**headers, "Content-Type": content_type},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        uploaded.raise_for_status()
    except OSFError:
        raise
    except (requests.RequestException, ValueError, KeyError) as exc:
        # The exception text can include URLs but never the token, which is only sent in a header.
        logger.warning("OSF deposit failed: %s", type(exc).__name__)
        raise OSFError("The deposit to OSF failed. Try again shortly.") from exc
    return OSFDeposit(node_id=node_id, url=url, file_name=file_name)
