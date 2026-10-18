"""Single message source shared with the Android build. No provider text passthrough."""
import json
import re
from importlib.resources import files

MESSAGES = json.loads(files('maoer_agent.resources').joinpath('error_catalog.json').read_text(encoding='utf-8'))


def error_message(code, **parameters):
    safe = code if re.fullmatch(r'[a-z][a-z0-9_]{0,79}', code) else 'unknown'
    values = {'code': safe, **parameters}
    return re.sub(r'\{([a-z_]+)\}', lambda m: str(values[m[1]]) if values.get(m[1]) is not None else MESSAGES['unknown_value'],
                  MESSAGES.get(code, MESSAGES['unknown_error']))
