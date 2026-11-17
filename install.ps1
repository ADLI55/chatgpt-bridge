# chatgpt-bridge one-line installer (Windows PowerShell):
#   powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/ADLI55/chatgpt-bridge/main/install.ps1 | iex"
# Installs uv if needed, installs chatgpt-bridge, then runs `chatgpt-bridge setup`
# (imports your newest ChatGPT export and registers it with Claude Code).
$ErrorActionPreference = "Stop"
$Source = "https://github.com/ADLI55/chatgpt-bridge/archive/refs/heads/main.zip"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv (Python package manager) ..."
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

Write-Host "Installing chatgpt-bridge ..."
uv tool install --force --quiet $Source
if ($LASTEXITCODE -ne 0) { throw "uv tool install failed" }

$Bin = Join-Path (uv tool dir --bin) "chatgpt-bridge.exe"
& $Bin setup @args
