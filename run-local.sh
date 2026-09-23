#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 was not found. Install Python 3 and run this script again."
  exit 1
fi

VENV_DIR="$PWD/.venv"
VENV_PY="$VENV_DIR/bin/python"

if [ ! -x "$VENV_PY" ]; then
  echo "Creating isolated virtual environment at:"
  echo "  $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

# Refuse to install anything unless this interpreter proves it is a venv.
"$VENV_PY" - <<'PY'
import sys
if sys.prefix == sys.base_prefix:
    raise SystemExit("ERROR: .venv is not active/valid; refusing to install packages.")
print(f"Verified isolated Python environment: {sys.prefix}")
PY

REQ_HASH=$(sha256sum requirements.txt | awk '{print $1}')
HASH_FILE="$VENV_DIR/.tracker_requirements_hash"
OLD_HASH=""
[ -f "$HASH_FILE" ] && OLD_HASH=$(cat "$HASH_FILE")
if [ "$REQ_HASH" != "$OLD_HASH" ]; then
  echo "Installing/updating tracker dependencies INSIDE .venv only..."
  "$VENV_PY" -m pip install -r requirements.txt
  printf '%s' "$REQ_HASH" > "$HASH_FILE"
fi

export APP_DATA_DIR="$PWD/data"

echo
echo "Flightline Tracker is starting from its isolated environment."
echo "Python: $VENV_PY"
echo "Open:   http://127.0.0.1:8080"
echo
echo "On a fresh data folder, the server will print a generated admin password below."
echo "Click Manage in the web page to unlock editing, then load a real award."
echo "Press Ctrl+C here to stop the tracker."
echo

exec "$VENV_PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8080
