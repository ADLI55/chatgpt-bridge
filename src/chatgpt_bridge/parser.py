"""Parse a ChatGPT data export (Settings -> Data controls -> Export data).

The export is a ZIP. Its conversations live in ``conversations.json`` (one JSON
array) or, in newer exports, in several ``conversations-NNN.json`` files. Each
conversation stores its messages as a tree in ``mapping``; the branch the user
last saw ends at ``current_node``. We follow that branch back to the root, which
is what the ChatGPT UI shows, and drop edited-away branches.
"""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

CONVERSATION_FILE = re.compile(r"^conversations(-\d+)?\.json$")

# channel values
MAIN = "main"  # what you see in the chat: user prompts and assistant replies
TOOL = "tool"  # code the model ran, tool output, browsing results
HIDDEN = "hidden"  # reasoning, custom instructions, memory, system prompts

_HIDDEN_CONTENT_TYPES = {
    "thoughts",
    "reasoning_recap",
    "user_editable_context",
    "model_editable_context",
}


@dataclass
class Message:
    role: str
    channel: str
    text: str
    create_time: float | None = None
    content_type: str = "text"


@dataclass
class Conversation:
    id: str
    title: str
    create_time: float | None
    update_time: float | None
    model: str | None
    messages: list[Message] = field(default_factory=list)
    gizmo_id: str | None = None
    is_archived: bool = False


def _part_to_text(part: Any) -> str:
    if isinstance(part, str):
        return part
    if not isinstance(part, dict):
        return ""
    kind = part.get("content_type", "")
    if isinstance(part.get("text"), str):  # audio_transcription and friends
        return part["text"]
    if "image" in kind:
        return "[image]"
    if "audio" in kind:
        return "[audio]"
    if "video" in kind:
        return "[video]"
    return "[attachment]"


def content_to_text(content: dict[str, Any] | None) -> str:
    """Flatten a message ``content`` object into plain text."""
    if not isinstance(content, dict):
        return ""
    kind = content.get("content_type", "text")

    if kind == "code":
        lang = content.get("language") or ""
        if lang == "unknown":
            lang = ""
        body = content.get("text") or ""
        return f"```{lang}\n{body}\n```" if body else ""
    if kind == "tether_quote":
        bits = [content.get("title"), content.get("url"), content.get("text")]
        return "\n".join(b for b in bits if isinstance(b, str) and b)
    if kind == "tether_browsing_display":
        return content.get("result") or content.get("summary") or ""
    if kind == "thoughts":
        thoughts = content.get("thoughts") or []
        return "\n\n".join(
            t.get("content", "") for t in thoughts if isinstance(t, dict) and t.get("content")
        )
    if kind == "reasoning_recap":
        return content.get("content") or ""
    if kind == "user_editable_context":
        bits = [content.get("user_profile"), content.get("user_instructions")]
        return "\n\n".join(b for b in bits if isinstance(b, str) and b)

    parts = content.get("parts")
    if isinstance(parts, list):
        return "\n".join(t for t in (_part_to_text(p) for p in parts) if t)
    for key in ("text", "result", "content"):
        if isinstance(content.get(key), str):
            return content[key]
    return ""


def _channel(message: dict[str, Any], role: str, kind: str) -> str:
    meta = message.get("metadata") or {}
    if meta.get("is_visually_hidden_from_conversation") or kind in _HIDDEN_CONTENT_TYPES:
        return HIDDEN
    if role == "system":
        return HIDDEN
    if role == "tool":
        return TOOL
    if role == "assistant":
        recipient = message.get("recipient")
        if recipient not in (None, "all"):
            return TOOL  # the model calling a tool (python, web, dalle, ...)
        if kind in ("code", "execution_output"):
            return TOOL
    return MAIN


def _active_branch(mapping: dict[str, Any], current: str | None) -> list[dict[str, Any]]:
    """Return message nodes from root to ``current`` (the branch shown in the UI)."""
    if not mapping:
        return []
    if current not in mapping:
        # Fall back to the most recently written leaf.
        leaves = [(k, n) for k, n in mapping.items() if not n.get("children")]
        leaves.sort(key=lambda kn: ((kn[1].get("message") or {}).get("create_time") or 0))
        current = leaves[-1][0] if leaves else next(iter(mapping))
    chain: list[dict[str, Any]] = []
    seen: set[str] = set()
    node_id: str | None = current
    while node_id and node_id in mapping and node_id not in seen:
        seen.add(node_id)
        node = mapping[node_id]
        chain.append(node)
        node_id = node.get("parent")
    chain.reverse()
    return chain


def parse_conversation(raw: dict[str, Any]) -> Conversation | None:
    conv_id = raw.get("conversation_id") or raw.get("id")
    if not conv_id:
        return None
    conv = Conversation(
        id=str(conv_id),
        title=(raw.get("title") or "").strip() or "(untitled)",
        create_time=raw.get("create_time"),
        update_time=raw.get("update_time") or raw.get("create_time"),
        model=raw.get("default_model_slug"),
        gizmo_id=raw.get("gizmo_id"),
        is_archived=bool(raw.get("is_archived")),
    )
    for node in _active_branch(raw.get("mapping") or {}, raw.get("current_node")):
        message = node.get("message")
        if not isinstance(message, dict):
            continue
        role = ((message.get("author") or {}).get("role")) or "unknown"
        content = message.get("content") or {}
        kind = content.get("content_type", "text") if isinstance(content, dict) else "text"
        text = content_to_text(content).strip()
        if not text:
            continue
        conv.messages.append(
            Message(
                role=role,
                channel=_channel(message, role, kind),
                text=text,
                create_time=message.get("create_time"),
                content_type=kind,
            )
        )
    return conv


def _load_json_array(data: bytes | str, source: str) -> list[dict[str, Any]]:
    obj = json.loads(data)
    if isinstance(obj, dict):
        obj = obj.get("conversations", [obj])
    if not isinstance(obj, list):
        raise ValueError(f"{source}: expected a JSON array of conversations")
    return [c for c in obj if isinstance(c, dict)]


def iter_raw_conversations(path: str | Path) -> Iterator[dict[str, Any]]:
    """Yield raw conversation dicts from a ZIP, a folder, or a JSON file."""
    path = Path(path).expanduser()
    if not path.exists():
        raise FileNotFoundError(path)

    if path.is_file() and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            names = sorted(
                n for n in zf.namelist() if CONVERSATION_FILE.match(Path(n).name) and "__MACOSX" not in n
            )
            if not names:
                raise ValueError(f"{path}: no conversations*.json inside this ZIP")
            for name in names:
                yield from _load_json_array(zf.read(name), f"{path}:{name}")
        return

    if path.is_dir():
        files = sorted(p for p in path.rglob("*.json") if CONVERSATION_FILE.match(p.name))
        if not files:
            raise ValueError(f"{path}: no conversations*.json in this folder")
        for f in files:
            yield from _load_json_array(f.read_bytes(), str(f))
        return

    yield from _load_json_array(path.read_bytes(), str(path))


def load_export(path: str | Path) -> list[Conversation]:
    """Parse every conversation in an export. Later files win on duplicate ids."""
    by_id: dict[str, Conversation] = {}
    for raw in iter_raw_conversations(path):
        conv = parse_conversation(raw)
        if conv is not None:
            by_id[conv.id] = conv
    return list(by_id.values())
