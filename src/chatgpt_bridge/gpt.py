"""Talk to GPT through the OpenAI Responses API.

Multi-turn threads use ``previous_response_id``, so OpenAI keeps the thread
state and we only send the new message each turn.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Sequence

# Model names change often; override with OPENAI_MODEL or the tool's `model` argument.
FALLBACK_MODEL = "gpt-5"

DEFAULT_INSTRUCTIONS = (
    "You are GPT, talking with Claude Code (another AI assistant) on behalf of the same user. "
    "Claude may ask for a second opinion, a review, or to pick up a conversation the user "
    "previously had with ChatGPT. Answer directly and concretely; say so when you disagree "
    "or are unsure."
)


def default_model() -> str:
    return os.environ.get("OPENAI_MODEL") or FALLBACK_MODEL


def context_char_budget() -> int:
    try:
        return int(os.environ.get("CHATGPT_BRIDGE_CONTEXT_CHARS", "60000"))
    except ValueError:
        return 60000


@dataclass
class GptReply:
    text: str
    thread_id: str
    model: str
    context_messages: int = 0


def build_context(messages: Sequence[Any], budget: int) -> tuple[list[dict[str, str]], bool]:
    """Turn stored messages (rows with role/channel/text) into Responses API input.

    Keeps only user/assistant chat turns, newest first, until ``budget`` characters.
    """
    picked: list[dict[str, str]] = []
    used = 0
    truncated = False
    for m in reversed(list(messages)):
        if m["channel"] != "main" or m["role"] not in ("user", "assistant"):
            continue
        text = m["text"]
        if used + len(text) > budget:
            truncated = True
            break
        picked.append({"role": m["role"], "content": text})
        used += len(text)
    picked.reverse()
    if truncated:
        picked.insert(
            0, {"role": "user", "content": "[Earlier messages of this conversation were omitted.]"}
        )
    return picked, truncated


def make_client() -> Any:
    from openai import AsyncOpenAI  # imported lazily so search works without it configured

    return AsyncOpenAI()  # reads OPENAI_API_KEY / OPENAI_BASE_URL


async def ask(
    message: str,
    *,
    thread_id: str | None = None,
    context: list[dict[str, str]] | None = None,
    model: str | None = None,
    instructions: str | None = None,
    client_factory: Callable[[], Any] = make_client,
) -> GptReply:
    client = client_factory()
    items: list[dict[str, str]] = list(context or [])
    items.append({"role": "user", "content": message})
    kwargs: dict[str, Any] = {
        "model": model or default_model(),
        "input": items,
        "instructions": instructions or DEFAULT_INSTRUCTIONS,
        "store": True,  # needed so the reply can be continued with previous_response_id
    }
    if thread_id:
        kwargs["previous_response_id"] = thread_id
    resp = await client.responses.create(**kwargs)
    return GptReply(
        text=resp.output_text or "",
        thread_id=resp.id,
        model=getattr(resp, "model", kwargs["model"]),
        context_messages=len(context or []),
    )
