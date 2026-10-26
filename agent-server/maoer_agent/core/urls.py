"""Public-source URL classification shared by readers and workflows."""
from ipaddress import ip_address
from urllib.parse import urlsplit
import re


def public_url(value):
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password:
            return False
        host = parsed.hostname.lower()
        if '.' not in host or host.endswith(('.local', '.localhost', '.internal')): return False
        try: return ip_address(host).is_global
        except ValueError: return True
    except ValueError:
        return False


def public_links(text):
    """Exact public URLs present in retrieved text, without guessing paths."""
    return {url for match in re.finditer(r'https?://[^\s<>\[\]()]+', text or '')
            if public_url(url := match[0].rstrip('。，；'))}


def known_public_url(value, known):
    """Keep an authoritative URL intact when a model omitted its query.

    Only a unique already-observed URL at the exact same origin/path can be
    recovered. Never discard or invent query values, paths or source identity.
    """
    if not public_url(value):
        return None
    if value in known:
        return value
    parsed = urlsplit(value)
    if parsed.query or parsed.fragment:
        return None
    matches = {url for url in known if public_url(url)
               and (candidate := urlsplit(url)).query
               and (candidate.scheme, candidate.netloc, candidate.path, candidate.fragment)
               == (parsed.scheme, parsed.netloc, parsed.path, '')}
    return next(iter(matches)) if len(matches) == 1 else None
