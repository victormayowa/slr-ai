import httpx
import os
from typing import Dict, Any

# Unpaywall requires an email for their API
UNPAYWALL_EMAIL = os.getenv("UNPAYWALL_EMAIL", "omni.review.bot@gmail.com")

async def check_unpaywall(doi: str) -> Dict[str, Any]:
    """
    Queries the Unpaywall API to see if a DOI is open access.
    """
    url = f"https://api.unpaywall.org/v2/{doi}?email={UNPAYWALL_EMAIL}"
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url)
            if response.status_code == 200:
                return response.json()
            return {"is_oa": False, "error": f"API returned {response.status_code}"}
        except httpx.RequestError as e:
            return {"is_oa": False, "error": str(e)}
