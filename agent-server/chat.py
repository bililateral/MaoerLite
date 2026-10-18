"""Local terminal client: python agent-server/chat.py."""
import sys
from maoer_agent.cli import main

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
