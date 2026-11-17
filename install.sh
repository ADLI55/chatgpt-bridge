#!/bin/sh
# chatgpt-bridge one-line installer (macOS / Linux):
#   curl -LsSf https://raw.githubusercontent.com/ADLI55/chatgpt-bridge/main/install.sh | sh
# Installs uv if needed, installs chatgpt-bridge, then runs `chatgpt-bridge setup`
# (imports your newest ChatGPT export and registers it with Claude Code).
# Arguments after `sh -s --` are passed to setup, e.g. `| sh -s -- --no-browser`.
set -eu

SOURCE="https://github.com/ADLI55/chatgpt-bridge/archive/refs/heads/main.zip"

if ! command -v uv >/dev/null 2>&1; then
    echo "Installing uv (Python package manager) ..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    export PATH
fi

echo "Installing chatgpt-bridge ..."
uv tool install --force --quiet "$SOURCE"

BIN="$(uv tool dir --bin)/chatgpt-bridge"
# Read answers from the terminal even though this script arrives through a pipe.
if [ -t 1 ] && (exec </dev/tty) 2>/dev/null; then
    "$BIN" setup "$@" </dev/tty
else
    "$BIN" setup --no-prompt "$@"
fi
