"""Local configuration. Secrets are never included in representations or errors."""
from dataclasses import dataclass, field
from pathlib import Path
from ipaddress import ip_address
import secrets
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = 'deepseek-v4-flash-0731'
DEFAULT_BASE_URL = 'https://www.dafangyuntu.com/ai/v1'


@dataclass(frozen=True)
class Settings:
    api_key: str = field(repr=False)
    access_token: str = field(repr=False)
    port: int = 8787
    provider: str = 'openai_compatible'
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    host: str = '127.0.0.1'

    @property
    def endpoint(self):
        return self.base_url.rstrip('/') + '/chat/completions'


def load_settings(path: Path = ROOT / '.local/agent.env') -> Settings:
    values = {}
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' not in line:
            raise ValueError('Invalid local configuration line')
        name, value = line.split('=', 1)
        values[name.strip()] = value.strip().removeprefix('"').removesuffix('"').removeprefix("'").removesuffix("'")
    provider = values.get('AGENT_PROVIDER', 'openai_compatible')
    if provider not in ('openai_compatible', 'zhipu'):
        raise ValueError('Invalid AGENT_PROVIDER')
    # Provider selection is explicit: never send the old provider key to a relay.
    key = values.get('AGENT_API_KEY' if provider == 'openai_compatible' else 'ZHIPU_API_KEY', '')
    if not key or any(c.isspace() for c in key) or not key.isascii():
        raise ValueError('Selected provider API key is missing or invalid')
    base_url = values.get('AGENT_BASE_URL', DEFAULT_BASE_URL).rstrip('/') if provider == 'openai_compatible' else 'https://open.bigmodel.cn/api/paas/v4'
    model = values.get('AGENT_MODEL', DEFAULT_MODEL) if provider == 'openai_compatible' else values.get('ZHIPU_MODEL', 'glm-4.7-flash')
    parsed = urlsplit(base_url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Invalid model service base URL')
    if not model or len(model) > 128 or not model.isascii() or any(c.isspace() for c in model):
        raise ValueError('Invalid model name')
    port = int(values.get('AGENT_PORT', '8787'))
    if not 1024 <= port <= 65535:
        raise ValueError('Invalid AGENT_PORT')
    host = values.get('AGENT_HOST', '127.0.0.1')
    address = ip_address(host)
    if address.version != 4 or not (address.is_loopback or any(
        host.startswith(prefix) for prefix in ('10.', '192.168.')
    ) or (host.startswith('172.') and 16 <= int(host.split('.')[1]) <= 31)):
        raise ValueError('AGENT_HOST must be a loopback or private IPv4 address')
    token_path = path.resolve().with_name('agent-server.token')
    try:
        with token_path.open('x', encoding='utf-8') as file:
            file.write(secrets.token_urlsafe(32))
    except FileExistsError:
        pass
    token = token_path.read_text(encoding='utf-8').strip()
    if len(token) < 32 or not token.isascii() or any(c.isspace() for c in token):
        raise ValueError('Invalid local access token file')
    return Settings(key, token, port, provider, base_url, model, host)
