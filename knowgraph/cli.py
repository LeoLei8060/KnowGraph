"""Command line interface for the local service."""

from __future__ import annotations

import argparse
import json
import sys

from .app import Application


def _print(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="knowgraph", description="Local-first personal knowledge manager")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Create data directories and the SQLite schema")
    serve = sub.add_parser("serve", help="Run the local web UI and API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")

    collect = sub.add_parser("collect", help="Run collection for one source or all sources")
    collect.add_argument("--source")
    sub.add_parser("sources", help="List configured sources")
    sub.add_parser("status", help="Show service status")

    search = sub.add_parser("search", help="Search the local knowledge base")
    search.add_argument("query")
    search.add_argument("--tags", default="")
    search.add_argument("--limit", type=int, default=10)

    ask = sub.add_parser("ask", help="Ask a grounded question")
    ask.add_argument("question")
    ask.add_argument("--local", action="store_true", help="Only answer from local evidence")

    review = sub.add_parser("review", help="Generate a weekly or monthly review")
    review.add_argument("--period", choices=["week", "month"], default="week")
    review.add_argument("--offset", type=int, default=-1)
    review.add_argument("--force", action="store_true")

    sub.add_parser("sync", help="Re-index Markdown edited in Obsidian")

    args = parser.parse_args(argv)
    app = Application()

    if args.command == "init":
        _print({"initialized": True, "home": str(app.paths.home), "vault": str(app.paths.vault)})
        return 0
    if args.command == "serve":
        import uvicorn

        uvicorn.run("knowgraph.api:create_app", factory=True, host=args.host, port=args.port, reload=args.reload)
        return 0
    if args.command == "status":
        _print(app.status())
        return 0
    if args.command == "sources":
        _print({"sources": app.sources.list()})
        return 0
    if args.command == "collect":
        if args.source:
            source = app.sources.get(args.source)
            if source is None:
                print(f"Unknown source: {args.source}", file=sys.stderr)
                return 2
            _print(app.ingest.collect_source(source))
        else:
            _print(app.ingest.collect_all())
        return 0
    if args.command == "search":
        _print(app.search.search(args.query, tags=args.tags, limit=args.limit))
        return 0
    if args.command == "ask":
        _print(app.qa.ask(args.question, mode="local" if args.local else "auto"))
        return 0
    if args.command == "review":
        _print(app.reviews.generate(args.period, offset=args.offset, force=args.force))
        return 0
    if args.command == "sync":
        _print(app.vault.sync_from_disk())
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
