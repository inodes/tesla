"""url_host.py - which site a web address belongs to, from its host name (never from a piece of the text, which "https://evil.example/tesla.com" also holds).

    on_host(url, "tesla.com")             -> True for tesla.com and any name under it (www.tesla.com); a path or query that merely says tesla.com is not
    on_host(url, "bppulse.com", "bp.com") -> True when it is on any of them
"""
from urllib.parse import urlparse


def host_of(url):
    """The lower-case host name of an address with or without its scheme ("www.tesla.com/findus" too), "" when it has none."""
    u = str(url or "").strip()
    if not u:
        return ""
    if "//" not in u:
        u = "//" + u
    try:
        return (urlparse(u).hostname or "").lower()
    except ValueError:
        return ""


def on_host(url, *domains):
    h = host_of(url)
    return bool(h) and any(h == d or h.endswith("." + d) for d in domains)
