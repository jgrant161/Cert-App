"""python -m consign serve [--share] [--port 8100] | python -m consign demo"""

from __future__ import annotations

import argparse
import socket


def _lan_address() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def main() -> None:
    parser = argparse.ArgumentParser(prog="consign")
    sub = parser.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve", help="run the portal")
    serve.add_argument("--port", type=int, default=8100)
    serve.add_argument("--share", action="store_true", help="let other computers on the shop network connect")
    sub.add_parser("demo", help="fill an empty database with sample consignors, items and sales")
    args = parser.parse_args()

    if args.cmd == "demo":
        from .demo import seed
        print(seed())
        return

    import uvicorn

    from .web.app import create_app
    host = "0.0.0.0" if args.share else "127.0.0.1"
    print(f"Open http://{_lan_address() if args.share else '127.0.0.1'}:{args.port}")
    uvicorn.run(create_app(), host=host, port=args.port, proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
