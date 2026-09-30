#!/usr/bin/env bash
set -euo pipefail
tool_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -n "${PYTHON:-}" ]]; then
	interpreter="$PYTHON"
elif [[ -x "$tool_directory/.venv/bin/python" ]]; then
	interpreter="$tool_directory/.venv/bin/python"
elif [[ -x "$tool_directory/.venv/Scripts/python.exe" ]]; then
	interpreter="$tool_directory/.venv/Scripts/python.exe"
else
	interpreter="python3"
fi
exec "$interpreter" "$tool_directory/audit.py" "$@"