"""Run from any working directory: python agent-server/main.py."""
import logging
import sys
import uvicorn

from app import create_app
from config import load_settings


def main():
    try:
        settings = load_settings()
    except (OSError, ValueError):
        print('Configuration failed: check .local/agent.env, base URL, model, key and port.', file=sys.stderr)
        return 1
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s %(message)s')
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port,
                access_log=False, log_level='info', timeout_graceful_shutdown=5)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
