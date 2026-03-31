#!/usr/bin/env python
import os
import sys

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")

    # Auto-inject port for runserver if no address specified
    if len(sys.argv) >= 2 and sys.argv[1] == "runserver" and len(sys.argv) == 2:
        from settings import PORT, TARGET_BASE_URL
        print(f"\n  Copilot Proxy listening on 0.0.0.0:{PORT}")
        print(f"  Proxying to: {TARGET_BASE_URL}\n")
        sys.argv.append(f"0.0.0.0:{PORT}")

    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)
