"""A synthetic ChatGPT export shaped like the real thing (tree mapping, branches,
hidden reasoning, tool calls, images, Chinese text)."""

from __future__ import annotations

import json
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from chatgpt_bridge.parser import load_export
from chatgpt_bridge.store import build_index


def ts(s: str) -> float:
    return datetime.strptime(s, "%Y-%m-%d").timestamp()


class Builder:
    def __init__(self, conv_id: str, title: str, day: str, model: str = "gpt-4o"):
        self.conv_id, self.title, self.t0 = conv_id, title, ts(day)
        self.model = model
        root = f"{conv_id}-root"
        self.mapping = {root: {"id": root, "message": None, "parent": None, "children": []}}
        self.last = root
        self.n = 0

    def add(self, role, content, parent=None, recipient="all", meta=None, weight=1.0):
        self.n += 1
        node_id = f"{self.conv_id}-n{self.n}"
        parent = parent or self.last
        self.mapping[node_id] = {
            "id": node_id,
            "message": {
                "id": node_id,
                "author": {"role": role, "name": None, "metadata": {}},
                "create_time": self.t0 + self.n * 60,
                "update_time": None,
                "content": content,
                "status": "finished_successfully",
                "end_turn": True,
                "weight": weight,
                "metadata": meta or {},
                "recipient": recipient,
            },
            "parent": parent,
            "children": [],
        }
        self.mapping[parent]["children"].append(node_id)
        self.last = node_id
        return node_id

    def text(self, role, *parts, **kw):
        return self.add(role, {"content_type": "text", "parts": list(parts)}, **kw)

    def build(self, current=None):
        return {
            "title": self.title,
            "create_time": self.t0,
            "update_time": self.t0 + self.n * 60,
            "mapping": self.mapping,
            "moderation_results": [],
            "current_node": current or self.last,
            "conversation_id": self.conv_id,
            "id": self.conv_id,
            "default_model_slug": self.model,
            "gizmo_id": None,
            "is_archived": False,
        }


def make_conversations() -> list[dict]:
    convs = []

    # A: Chinese data-cleaning chat with an edited (abandoned) branch, hidden
    # system prompt, reasoning, a python tool call and an image upload.
    a = Builder("aaaa1111-0000-0000-0000-000000000001", "Python 数据清洗", "2024-03-10")
    a.text("system", "", meta={"is_visually_hidden_from_conversation": True})
    sys_node = a.last
    a.text("user", "旧的问题：这条消息后来被编辑掉了 abandonedbranch")
    a.text("assistant", "旧分支的回答")
    a.text("user", "我想用 pandas 清洗安全事故数据，怎么处理缺失值？", parent=sys_node)
    a.add(
        "assistant",
        {"content_type": "thoughts", "thoughts": [{"summary": "s", "content": "secretreasoning 思考过程"}]},
    )
    a.add("assistant", {"content_type": "code", "language": "python", "text": "import pandas as pd\nzzcodeonly"},
          recipient="python")
    a.add("tool", {"content_type": "execution_output", "text": "toolresultxyz"})
    a.text("assistant", "可以先用 df.isna().sum() 看缺失情况，再用 df.fillna() 填充。")
    a.add(
        "user",
        {
            "content_type": "multimodal_text",
            "parts": [
                {"content_type": "image_asset_pointer", "asset_pointer": "file-service://x"},
                "这张图里的表格能帮我看看吗",
            ],
        },
    )
    a.text("assistant", "表格里第三列有很多空值。")
    convs.append(a.build())

    # B: English chat.
    b = Builder("bbbb2222-0000-0000-0000-000000000002", "Trip planning to Kyoto", "2023-07-01")
    b.text("user", "Which temples should I visit in Kyoto?")
    b.text("assistant", "Kinkaku-ji, Fushimi Inari and Kiyomizu-dera are the classic temples.")
    convs.append(b.build())

    # C: long conversation for paging.
    c = Builder("cccc3333-0000-0000-0000-000000000003", "Long chat", "2025-01-05")
    for i in range(30):
        c.text("user" if i % 2 == 0 else "assistant", f"message {i} " + "lorem ipsum " * 80)
    convs.append(c.build())

    # D: the keyword only appears in the title.
    d = Builder("dddd4444-0000-0000-0000-000000000004", "PostgreSQL 索引优化", "2024-11-20")
    d.text("user", "How do I make this query faster?")
    d.text("assistant", "Add a btree on the filter column.")
    convs.append(d.build())
    return convs


@pytest.fixture
def conversations() -> list[dict]:
    return make_conversations()


@pytest.fixture
def export_zip(tmp_path: Path, conversations) -> Path:
    path = tmp_path / "chatgpt-export.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("conversations.json", json.dumps(conversations, ensure_ascii=False))
        zf.writestr("chat.html", "<html></html>")
        zf.writestr("user.json", "{}")
    return path


@pytest.fixture
def db(tmp_path: Path, export_zip: Path) -> Path:
    path = tmp_path / "index" / "history.db"
    build_index(load_export(export_zip), path, source=str(export_zip))
    return path
