"""Start the self-contained SI Forge workbench demo (no AEDT access)."""
from __future__ import annotations

import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import webbrowser


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the SI Forge demo workbench.")
    parser.add_argument("--port", type=int, default=8769)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    handler = lambda *a, **kw: SimpleHTTPRequestHandler(*a, directory=str(root), **kw)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"SI Forge demo workbench: {url}")
    print("Demo only: no AEDT project or production Mission is accessed.")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
