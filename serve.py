#!/usr/bin/env python3
"""
Start the frisbee-rankings local viewer.

Usage
-----
    python serve.py              # serve on port 8765
    python serve.py --port 9000  # use a different port
"""
import argparse
import http.server
from pathlib import Path

BASE = Path(__file__).parent


class _Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE), **kwargs)

    def log_message(self, fmt, *args):
        pass  # suppress per-request noise


class _Server(http.server.HTTPServer):
    def handle_error(self, request, client_address):
        import sys
        if not isinstance(sys.exc_info()[1], BrokenPipeError):
            super().handle_error(request, client_address)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the frisbee-rankings viewer.")
    parser.add_argument("--port", type=int, default=8765, help="Port (default: 8765)")
    args = parser.parse_args()

    with _Server(("", args.port), _Handler) as srv:
        base = f"http://localhost:{args.port}"
        print(f"\n  Open : {base}/viewer/index.html")
        print(f"\n  Press Ctrl+C to stop.\n")

        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
