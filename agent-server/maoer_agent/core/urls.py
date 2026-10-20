"""Public-source URL classification shared by readers and workflows."""
from ipaddress import ip_address
from urllib.parse import urlsplit


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
