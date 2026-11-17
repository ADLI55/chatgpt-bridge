from types import SimpleNamespace

from conftest import write_export

from chatgpt_bridge import setup_cmd
from chatgpt_bridge.setup_cmd import run_setup, server_command
from chatgpt_bridge.store import History


class FakeRunner:
    def __init__(self, returncode=0):
        self.calls = []
        self.returncode = returncode

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        return SimpleNamespace(returncode=self.returncode, stdout="Added stdio MCP server", stderr="")


def test_setup_imports_and_registers(tmp_path, conversations, isolated_watch_dirs, monkeypatch):
    write_export(isolated_watch_dirs / "export.zip", conversations)
    opened = []
    monkeypatch.setattr(setup_cmd.webbrowser, "open", opened.append)
    runner = FakeRunner()
    lines = []
    db = tmp_path / "h.db"
    code = run_setup(db=db, api_key="sk-test", model="gpt-x", claude="/bin/claude",
                     runner=runner, interactive=False, out=lines.append)

    assert code == 0
    assert History(db).stats()["conversations"] == 4
    assert opened == []
    remove, add = runner.calls
    assert remove == ["/bin/claude", "mcp", "remove", "chatgpt", "-s", "user"]
    assert add[:6] == ["/bin/claude", "mcp", "add", "chatgpt", "-s", "user"]
    assert "OPENAI_API_KEY=sk-test" in add and "OPENAI_MODEL=gpt-x" in add
    sep = add.index("--")
    assert add[sep + 1:][-1] == "serve" and str(db.absolute()) in add[sep + 1:]
    assert any("✅" in line for line in lines)


def test_setup_without_export_opens_export_page(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(setup_cmd.webbrowser, "open", opened.append)
    runner = FakeRunner()
    code = run_setup(db=tmp_path / "h.db", claude="/bin/claude", runner=runner,
                     interactive=False, out=lambda s: None)
    assert code == 0
    assert opened == [setup_cmd.EXPORT_PAGE]
    add = runner.calls[-1]
    assert not any(a.startswith("OPENAI_API_KEY") for a in add)


def test_setup_asks_for_key_when_interactive(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_cmd.getpass, "getpass", lambda prompt: "  sk-typed  ")
    runner = FakeRunner()
    run_setup(db=tmp_path / "h.db", claude="/bin/claude", runner=runner,
              interactive=True, open_browser=False, out=lambda s: None)
    assert "OPENAI_API_KEY=sk-typed" in runner.calls[-1]


def test_setup_without_claude_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_cmd.shutil, "which", lambda name: None)
    lines = []
    code = run_setup(db=tmp_path / "h.db", interactive=False, open_browser=False, out=lines.append)
    assert code == 1
    assert any("claude mcp add chatgpt" in line for line in lines)


def test_setup_reports_claude_failure(tmp_path):
    code = run_setup(db=tmp_path / "h.db", claude="/bin/claude", runner=FakeRunner(returncode=1),
                     interactive=False, open_browser=False, out=lambda s: None)
    assert code == 1


def test_server_command_default_db_has_no_flag():
    assert server_command(setup_cmd.BUILTIN_DB)[-1] == "serve"
    assert "--db" not in server_command(setup_cmd.BUILTIN_DB)
