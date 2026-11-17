"""Command line: import an export, run the MCP server, or search from the terminal."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .store import History, HistoryNotImported, build_index, default_db_path, format_time


def _cmd_import(args: argparse.Namespace) -> int:
    from .parser import load_export

    src = Path(args.export).expanduser()
    print(f"Reading {src} ...", file=sys.stderr)
    conversations = load_export(src)
    result = build_index(conversations, args.db, source=str(src.resolve()))
    print(
        f"Imported {result['conversations']} conversations ({result['messages']} messages) "
        f"into {result['db']}" + ("" if result["fts"] else " [full-text index unavailable; using LIKE]")
    )
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    from .server import run

    run(args.db)
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    from .server import format_search

    hits = History(args.db).search(" ".join(args.query), limit=args.limit)
    print(format_search(hits, " ".join(args.query)))
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    from .server import format_conversation

    conv, msgs = History(args.db).get_conversation(args.conversation_id)
    print(format_conversation(conv, msgs, 0, 10**9, args.tools))
    return 0


def _cmd_stats(args: argparse.Namespace) -> int:
    s = History(args.db).stats()
    print(
        f"{s['conversations']} conversations, {s['messages']} messages\n"
        f"range:    {format_time(s['first'])} → {format_time(s['last'])}\n"
        f"imported: {format_time(s['imported_at'])} from {s['source']}\n"
        f"db:       {History(args.db).db_path}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="chatgpt-bridge",
        description="Let Claude Code search your ChatGPT history and talk to GPT (MCP server).",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help=f"index location (default: $CHATGPT_BRIDGE_DB or {default_db_path()})",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("import", help="index a ChatGPT data export (.zip, folder or conversations.json)")
    p.add_argument("export")
    p.set_defaults(func=_cmd_import)

    p = sub.add_parser("serve", help="run the MCP server over stdio (what Claude Code launches)")
    p.set_defaults(func=_cmd_serve)

    p = sub.add_parser("search", help="search from the terminal")
    p.add_argument("query", nargs="+")
    p.add_argument("-n", "--limit", type=int, default=10)
    p.set_defaults(func=_cmd_search)

    p = sub.add_parser("show", help="print one conversation")
    p.add_argument("conversation_id")
    p.add_argument("--tools", action="store_true", help="include tool calls and output")
    p.set_defaults(func=_cmd_show)

    p = sub.add_parser("stats", help="summary of the imported history")
    p.set_defaults(func=_cmd_stats)

    args = parser.parse_args(argv)
    if args.db is None:
        args.db = default_db_path()
    try:
        return args.func(args)
    except (HistoryNotImported, FileNotFoundError, KeyError, ValueError) as e:
        msg = e.args[0] if isinstance(e, KeyError) and e.args else e
        print(f"error: {msg}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
