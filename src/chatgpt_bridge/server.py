"""MCP server exposing ChatGPT history search and a GPT chat line to Claude Code."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Callable, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from . import gpt
from .autoimport import AutoImporter
from .store import History, HistoryNotImported, format_time

INSTRUCTIONS = """\
Gives you the user's past ChatGPT conversations (from their ChatGPT data export) and a direct line to GPT.

- When the user mentions something they discussed, asked, wrote or decided with ChatGPT before, call \
search_chatgpt_history, then read_chatgpt_conversation for the full thread. Don't guess what was said.
- Search with a few short keywords in the language the user chatted in (Chinese chats need Chinese \
keywords). All terms must appear in the conversation (title or messages); drop terms if nothing matches.
- New exports the user downloads (Downloads/Desktop/Documents) are imported automatically. If the \
user says they saved an export elsewhere, call import_chatgpt_export with its path. If no history is \
imported yet, tell them to open https://chatgpt.com/#settings/DataControls, click Export data, and \
download the ZIP from OpenAI's email.
- ask_chatgpt sends text to OpenAI. Use it when the user wants GPT's opinion, a second review, or to \
continue an old ChatGPT conversation (pass context_conversation_id). Keep the returned thread_id to \
keep talking to GPT in the same thread.
"""

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)


def _fail(exc: Exception) -> ToolError:
    return ToolError(str(exc))


def format_search(hits: list, query: str) -> str:
    if not hits:
        return (
            f"No conversations match {query!r}. Try fewer or different keywords, "
            "synonyms, or the other language (e.g. Chinese vs English)."
        )
    lines = [f"{len(hits)} conversation(s) match {query!r}:\n"]
    for i, h in enumerate(hits, 1):
        lines.append(
            f"{i}. **{h.title}**\n"
            f"   id: {h.id} | {format_time(h.create_time)} → {format_time(h.update_time)} "
            f"| {h.message_count} messages{' | title match' if h.title_match else ''}"
        )
        for s in h.snippets:
            lines.append(f"   - [#{s.seq} {s.role}] {s.text}")
        lines.append("")
    lines.append("Use read_chatgpt_conversation(conversation_id=...) to read one in full.")
    return "\n".join(lines)


def format_list(rows: list, total: int, offset: int, stats: dict) -> str:
    head = (
        f"ChatGPT history: {stats['conversations']} conversations, {stats['messages']} messages, "
        f"{format_time(stats['first'])} → {format_time(stats['last'])} "
        f"(imported {format_time(stats['imported_at'])}).\n"
        f"Showing {offset + 1}-{offset + len(rows)} of {total} matching, newest first:\n"
    )
    if not rows:
        return head + "\n(none)"
    body = [
        f"- **{r['title']}** — {format_time(r['update_time'] or r['create_time'])}, "
        f"{r['message_count']} msgs, id: {r['id']}"
        for r in rows
    ]
    more = ""
    if offset + len(rows) < total:
        more = f"\n\nMore available: call again with offset={offset + len(rows)}."
    return head + "\n".join(body) + more


def format_conversation(conv, msgs: list, start: int, max_chars: int, include_tools: bool) -> str:
    shown = [m for m in msgs if m["channel"] == "main" or (include_tools and m["channel"] == "tool")]
    head = (
        f"# {conv['title']}\n"
        f"id: {conv['id']} | started {format_time(conv['create_time'])} | "
        f"last updated {format_time(conv['update_time'])}"
        + (f" | model: {conv['model']}" if conv["model"] else "")
        + f"\nLink: https://chatgpt.com/c/{conv['id']}\n"
    )
    out = [head]
    used = len(head)
    next_seq = None
    for m in shown:
        if m["seq"] < start:
            continue
        label = {"user": "User", "assistant": "ChatGPT", "tool": "Tool"}.get(m["role"], m["role"])
        block = f"\n## [#{m['seq']}] {label} · {format_time(m['create_time'])}\n\n{m['text']}\n"
        if used + len(block) > max_chars and len(out) > 1:
            next_seq = m["seq"]
            break
        if used + len(block) > max_chars:  # a single huge message: cut it
            block = block[: max(0, max_chars - used)] + "\n…[message truncated; call again with a larger max_chars]\n"
        out.append(block)
        used += len(block)
    if len(out) == 1:
        out.append("\n(no messages at or after this position)")
    if next_seq is not None:
        out.append(f"\n---\nTruncated. Continue with start={next_seq}.")
    return "".join(out)


def _with_note(note: str | None, text: str) -> str:
    return f"{note}\n\n{text}" if note else text


def create_server(
    db_path: Path | str | None = None,
    gpt_client_factory: Callable[[], Any] | None = None,
    watch_dirs: list[Path] | None = None,
    auto_import: bool | None = None,
    check_at_start: bool = False,
) -> MCPServer:
    history = History(db_path)
    auto = AutoImporter(history, dirs=watch_dirs, enabled=auto_import)
    client_factory = gpt_client_factory or gpt.make_client
    mcp = MCPServer("chatgpt-bridge", instructions=INSTRUCTIONS)
    if check_at_start:
        auto.check_in_background()

    @mcp.tool(annotations=READ_ONLY)
    def search_chatgpt_history(
        query: Annotated[
            str,
            Field(description="Keywords; every term must match. Use \"double quotes\" for a phrase."),
        ],
        limit: Annotated[int, Field(ge=1, le=50, description="Max conversations to return")] = 10,
        role: Annotated[
            Literal["any", "user", "assistant"],
            Field(description="Only match what the user wrote, or what ChatGPT replied"),
        ] = "any",
        since: Annotated[str | None, Field(description="YYYY, YYYY-MM or YYYY-MM-DD")] = None,
        until: Annotated[str | None, Field(description="YYYY, YYYY-MM or YYYY-MM-DD (inclusive)")] = None,
    ) -> str:
        """Search the user's past ChatGPT conversations by keyword.

        Returns matching conversations (best first) with ids, dates and snippets.
        Works for Chinese and English.
        """
        note = auto.check()
        try:
            hits = history.search(query, limit=limit, role=role, since=since, until=until)
        except (HistoryNotImported, ValueError) as e:
            raise _fail(e) from e
        return _with_note(note, format_search(hits, query))

    @mcp.tool(annotations=READ_ONLY)
    def list_chatgpt_conversations(
        limit: Annotated[int, Field(ge=1, le=200)] = 30,
        offset: Annotated[int, Field(ge=0)] = 0,
        since: Annotated[str | None, Field(description="YYYY, YYYY-MM or YYYY-MM-DD")] = None,
        until: Annotated[str | None, Field(description="YYYY, YYYY-MM or YYYY-MM-DD (inclusive)")] = None,
        title_contains: str | None = None,
    ) -> str:
        """List past ChatGPT conversations, newest first, with an overview of the history.

        Use it to browse by date ("what did I ask ChatGPT last March?") or by title.
        """
        note = auto.check()
        try:
            stats = history.stats()
            rows, total = history.list_conversations(limit, offset, since, until, title_contains)
        except (HistoryNotImported, ValueError) as e:
            raise _fail(e) from e
        return _with_note(note, format_list(rows, total, offset, stats))

    @mcp.tool(annotations=READ_ONLY)
    def read_chatgpt_conversation(
        conversation_id: Annotated[
            str, Field(description="Full id, a unique prefix of it, or a chatgpt.com/c/... link")
        ],
        start: Annotated[int, Field(ge=0, description="Message number (#) to start from")] = 0,
        max_chars: Annotated[int, Field(ge=2000, le=200000)] = 40000,
        include_tools: Annotated[
            bool, Field(description="Also show code ChatGPT ran, tool output and browsing results")
        ] = False,
    ) -> str:
        """Read a past ChatGPT conversation as a transcript (long ones are paged with `start`)."""
        note = auto.check()
        try:
            conv, msgs = history.get_conversation(conversation_id)
        except (HistoryNotImported, KeyError) as e:
            raise _fail(e) from e
        return _with_note(note, format_conversation(conv, msgs, start, max_chars, include_tools))

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False))
    def import_chatgpt_export(
        path: Annotated[
            str | None,
            Field(description="Export ZIP or unzipped folder. Omit to use the newest one in Downloads/Desktop/Documents"),
        ] = None,
    ) -> str:
        """Import or refresh the ChatGPT history from a ChatGPT data export (replaces the previous import)."""
        try:
            src, result = auto.import_now(path)
        except (FileNotFoundError, ValueError) as e:
            raise _fail(e) from e
        return (
            f"Imported {result['conversations']} conversations ({result['messages']} messages) "
            f"from {src}."
        )

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=True))
    async def ask_chatgpt(
        message: Annotated[str, Field(description="What to say to GPT")],
        thread_id: Annotated[
            str | None, Field(description="thread_id from an earlier ask_chatgpt reply, to continue that thread")
        ] = None,
        context_conversation_id: Annotated[
            str | None,
            Field(description="Start a new thread seeded with this past ChatGPT conversation, to pick it up"),
        ] = None,
        model: Annotated[str | None, Field(description="OpenAI model id; defaults to $OPENAI_MODEL")] = None,
        instructions: Annotated[str | None, Field(description="Optional system instructions for GPT")] = None,
    ) -> str:
        """Send a message to GPT via the OpenAI API and return its reply.

        This sends `message` (and, with context_conversation_id, that old conversation) to OpenAI.
        """
        if thread_id and context_conversation_id:
            raise ToolError("Pass either thread_id (continue a GPT thread) or context_conversation_id, not both.")

        context: list[dict[str, str]] = []
        truncated = False
        if context_conversation_id:
            try:
                _, msgs = history.get_conversation(context_conversation_id)
            except (HistoryNotImported, KeyError) as e:
                raise _fail(e) from e
            context, truncated = gpt.build_context(msgs, gpt.context_char_budget())

        try:
            reply = await gpt.ask(
                message,
                thread_id=thread_id,
                context=context,
                model=model,
                instructions=instructions,
                client_factory=client_factory,
            )
        except Exception as e:  # openai errors, missing key, network
            name = type(e).__name__
            hint = ""
            if "api_key" in str(e).lower() or name in ("OpenAIError", "AuthenticationError"):
                hint = " Set OPENAI_API_KEY in the MCP server's environment."
            elif name == "NotFoundError":
                hint = " If the model is unknown, set OPENAI_MODEL or pass `model`."
            elif name == "ModuleNotFoundError":
                hint = " Install the openai package."
            raise ToolError(f"ask_chatgpt failed ({name}): {e}.{hint}") from e

        note = ""
        if context_conversation_id:
            note = f"\n(Seeded with {reply.context_messages} message(s) from the old conversation"
            note += ", oldest ones omitted to fit.)" if truncated else ".)"
        return (
            f"{reply.text}\n\n---\n"
            f"GPT model: {reply.model} | thread_id: {reply.thread_id}{note}\n"
            "Pass this thread_id to ask_chatgpt to continue the conversation."
        )

    return mcp


def run(db_path: Path | str | None = None) -> None:
    create_server(db_path, check_at_start=True).run("stdio")
