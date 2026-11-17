"""End-to-end through a real MCP client connected in-process."""

import asyncio
import sys
from types import SimpleNamespace

from mcp import Client, StdioServerParameters

from chatgpt_bridge.server import create_server

A = "aaaa1111-0000-0000-0000-000000000001"


class FakeResponses:
    def __init__(self):
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        n = len(self.calls)
        return SimpleNamespace(id=f"resp_{n}", model=kwargs["model"], output_text=f"GPT reply {n}")


def call(server, name, args):
    async def go():
        async with Client(server) as client:
            return await client.call_tool(name, args)

    return asyncio.run(go())


def text(result):
    return "".join(c.text for c in result.content if getattr(c, "text", None))


def test_tools_listed(db):
    async def go():
        async with Client(create_server(db)) as client:
            return await client.list_tools()

    tools = {t.name: t for t in asyncio.run(go()).tools}
    assert set(tools) == {
        "search_chatgpt_history",
        "list_chatgpt_conversations",
        "read_chatgpt_conversation",
        "ask_chatgpt",
    }
    assert tools["search_chatgpt_history"].annotations.read_only_hint is True
    assert "role" in tools["search_chatgpt_history"].input_schema["properties"]


def test_search_tool(db):
    r = call(create_server(db), "search_chatgpt_history", {"query": "缺失值"})
    assert not r.is_error
    out = text(r)
    assert "Python 数据清洗" in out and A in out


def test_list_tool(db):
    out = text(call(create_server(db), "list_chatgpt_conversations", {"limit": 2}))
    assert "4 conversations" in out and "offset=2" in out


def test_read_tool(db):
    out = text(call(create_server(db), "read_chatgpt_conversation", {"conversation_id": "aaaa"}))
    assert "ChatGPT" in out and "fillna" in out and f"https://chatgpt.com/c/{A}" in out


def test_errors_are_tool_errors(tmp_path):
    r = call(create_server(tmp_path / "none.db"), "search_chatgpt_history", {"query": "x"})
    assert r.is_error and "chatgpt-bridge import" in text(r)


def test_ask_chatgpt_with_old_conversation_then_continue(db):
    fake = FakeResponses()
    server = create_server(db, gpt_client_factory=lambda: SimpleNamespace(responses=fake))

    r = call(server, "ask_chatgpt", {"message": "继续这个话题", "context_conversation_id": A, "model": "m1"})
    assert not r.is_error, text(r)
    assert "GPT reply 1" in text(r) and "thread_id: resp_1" in text(r)
    first = fake.calls[0]
    roles = [i["role"] for i in first["input"]]
    assert roles == ["user", "assistant", "user", "assistant", "user"]
    assert first["input"][-1]["content"] == "继续这个话题"
    assert "previous_response_id" not in first and first["store"] is True

    r = call(server, "ask_chatgpt", {"message": "then what?", "thread_id": "resp_1", "model": "m1"})
    second = fake.calls[1]
    assert second["previous_response_id"] == "resp_1"
    assert second["input"] == [{"role": "user", "content": "then what?"}]


def test_ask_chatgpt_rejects_both_ids(db):
    server = create_server(db, gpt_client_factory=lambda: SimpleNamespace(responses=FakeResponses()))
    r = call(server, "ask_chatgpt", {"message": "x", "thread_id": "a", "context_conversation_id": A})
    assert r.is_error


def test_ask_chatgpt_without_key(db, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    r = call(create_server(db), "ask_chatgpt", {"message": "hi"})
    assert r.is_error and "OPENAI_API_KEY" in text(r)


def test_stdio_server_process(db):
    """Launch `chatgpt-bridge serve` the way Claude Code does and talk over stdio."""
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "chatgpt_bridge", "--db", str(db), "serve"]
    )

    async def go():
        async with Client(params) as client:
            return await client.call_tool("search_chatgpt_history", {"query": "Kyoto"})

    r = asyncio.run(go())
    assert not r.is_error and "Trip planning to Kyoto" in text(r)
