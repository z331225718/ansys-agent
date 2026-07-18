from __future__ import annotations

import argparse
import json

from aedt_agent.knowledge.api_memory import AnsysApiMemory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ansys-api-memory")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--force", action="store_true")
    subparsers.add_parser("status")
    search = subparsers.add_parser("search")
    search.add_argument("query")
    search.add_argument("--package", choices=("auto", "pyaedt", "pyedb"), default="auto")
    search.add_argument("--limit", type=int, default=10)
    args = parser.parse_args(argv)
    memory = AnsysApiMemory()
    if args.command == "prepare":
        result = memory.prepare(force=args.force)
    elif args.command == "status":
        result = memory.status()
    else:
        result = memory.search(args.query, package=args.package, limit=args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
