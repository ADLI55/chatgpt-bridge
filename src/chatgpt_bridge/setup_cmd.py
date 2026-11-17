"""`chatgpt-bridge setup`: everything in one step.

1. import the newest ChatGPT export found in Downloads/Desktop/Documents
   (or open the ChatGPT export page if there is none yet),
2. optionally take an OpenAI API key for ask_chatgpt,
3. register the MCP server with Claude Code (`claude mcp add`).
"""

from __future__ import annotations

import getpass
import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Callable

from .autoimport import find_latest_export, import_export, watch_dirs

EXPORT_PAGE = "https://chatgpt.com/#settings/DataControls"
BUILTIN_DB = Path.home() / ".chatgpt-bridge" / "history.db"


def server_command(db: Path | None = None) -> list[str]:
    """How Claude Code should launch the server: an absolute path, so PATH doesn't matter."""
    exe = shutil.which("chatgpt-bridge")
    cmd = [str(Path(exe).absolute())] if exe else [sys.executable, "-m", "chatgpt_bridge"]
    if db is not None and Path(db).expanduser().absolute() != BUILTIN_DB.absolute():
        cmd += ["--db", str(Path(db).expanduser().absolute())]
    return cmd + ["serve"]


def register_with_claude(
    name: str,
    scope: str,
    env: dict[str, str],
    command: list[str],
    claude: str | None = None,
    runner: Callable = subprocess.run,
) -> tuple[bool, str]:
    claude = claude or shutil.which("claude")
    if not claude:
        env_flags = "".join(f" -e {k}=..." for k in env)
        manual = f"claude mcp add {name} -s {scope}{env_flags} -- " + " ".join(f'"{c}"' for c in command)
        return False, f"没有找到 claude 命令（Claude Code 没装或不在 PATH 里）。装好后运行：\n  {manual}"
    # Replace any earlier registration so re-running setup is safe.
    runner([claude, "mcp", "remove", name, "-s", scope], capture_output=True, text=True)
    args = [claude, "mcp", "add", name, "-s", scope]
    for key, value in env.items():
        args += ["-e", f"{key}={value}"]
    args += ["--", *command]
    result = runner(args, capture_output=True, text=True)
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    return result.returncode == 0, output


def run_setup(
    export: str | None = None,
    *,
    db: Path | None = None,
    api_key: str | None = None,
    model: str | None = None,
    name: str = "chatgpt",
    scope: str = "user",
    open_browser: bool = True,
    interactive: bool | None = None,
    claude: str | None = None,
    runner: Callable = subprocess.run,
    dirs: list[Path] | None = None,
    out: Callable[[str], None] = print,
) -> int:
    if interactive is None:
        interactive = sys.stdin.isatty()
    db_path = Path(db).expanduser() if db else BUILTIN_DB
    summary: list[str] = []

    # 1. history
    out("① 查找 ChatGPT 导出文件 ...")
    src = None
    try:
        if export:
            src = Path(export).expanduser()
            result = import_export(src, db_path)
        else:
            found = find_latest_export(dirs)
            result = None
            if found:
                src, mtime = found
                result = import_export(src, db_path, mtime)
    except (FileNotFoundError, ValueError) as e:
        out(f"   导入失败：{e}")
        return 1
    if result:
        out(f"   已导入 {src}：{result['conversations']} 段对话，{result['messages']} 条消息")
        summary.append(f"聊天记录：已导入 {result['conversations']} 段对话")
    else:
        folders = "、".join(str(d) for d in (dirs or watch_dirs()))
        out(f"   在 {folders} 里没找到导出文件。")
        out(f"   请在 ChatGPT 点「导出数据」：{EXPORT_PAGE}")
        out("   OpenAI 会发邮件给你，点邮件里的链接下载 zip 到「下载」文件夹，之后会自动导入。")
        if open_browser:
            try:
                webbrowser.open(EXPORT_PAGE)
                out("   （已在浏览器里打开这个页面）")
            except Exception:
                pass
        summary.append("聊天记录：等你导出，下载到「下载」文件夹后会自动导入")

    # 2. OpenAI key
    env: dict[str, str] = {}
    key = api_key or os.environ.get("OPENAI_API_KEY")
    if not key and interactive:
        out("② 想让 Claude 能直接和 GPT 对话的话，需要 OpenAI API key")
        out("   （在 https://platform.openai.com/api-keys 创建，按量计费，和 ChatGPT Plus 订阅不通用）")
        try:
            key = getpass.getpass("   粘贴 key 后回车，或直接回车跳过：").strip()
        except (EOFError, KeyboardInterrupt):
            key = ""
    if key:
        env["OPENAI_API_KEY"] = key
        if not interactive or api_key or os.environ.get("OPENAI_API_KEY"):
            out("② OpenAI API key：已设置")
        summary.append("和 GPT 对话：已启用")
    else:
        if not interactive:
            out("② OpenAI API key：未设置，跳过")
        summary.append("和 GPT 对话：未设置 key（以后运行 chatgpt-bridge setup --api-key sk-... 即可开启）")
    model = model or os.environ.get("OPENAI_MODEL")
    if model:
        env["OPENAI_MODEL"] = model

    # 3. Claude Code
    out("③ 注册到 Claude Code ...")
    ok, msg = register_with_claude(name, scope, env, server_command(db_path), claude=claude, runner=runner)
    if not ok:
        out(f"   {msg}")
        return 1
    out(f"   已添加 MCP 服务器 “{name}”" + ("（所有项目都能用）" if scope == "user" else f"（scope: {scope}）"))
    summary.append(f"Claude Code：已注册 “{name}”，新开的 Claude Code 会话里就能用")

    out("\n✅ 完成")
    for line in summary:
        out(f"   - {line}")
    out("\n打开 Claude Code，直接说：「搜一下我以前问 ChatGPT 的 ……」")
    return 0
