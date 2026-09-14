from urllib.parse import quote_plus

def build_ezproxy_url(doi: str, proxy_base_url: str) -> str:
    """
    Constructs an institutional proxy URL to bypass paywalls.
    For example, converting a DOI resolver URL into an EZProxy URL:
    DOI: 10.1038/s41586-020-2649-2
    Proxy: https://ezproxy.university.edu/login?url=
    Result: https://ezproxy.university.edu/login?url=https://doi.org/10.1038/s41586-020-2649-2
    """
    doi_resolver = f"https://doi.org/{doi}"
    
    # Ensure proxy base URL ends correctly
    if not proxy_base_url.endswith("url="):
        # Some proxies use different formats, assuming standard EZProxy format here
        if "?" in proxy_base_url:
            proxy_base_url = f"{proxy_base_url}&url="
        else:
            proxy_base_url = f"{proxy_base_url}?url="
            
    encoded_target = quote_plus(doi_resolver)
    return f"{proxy_base_url}{doi_resolver}" # Note: Some ezproxies require the target to NOT be URL encoded, some do. 
