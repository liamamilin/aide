#!/bin/bash
# upgrade_smoke_test.sh — migrate a synthetic schema-0 database with the packaged app.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EXECUTABLE="${ROOT}/dist/AI桌面助手.app/Contents/MacOS/AI桌面助手"
PYTHON_BIN="${PYTHON:-python3}"
TEMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/aide-upgrade-smoke.XXXXXX")"
DATA_DIR="${TEMP_ROOT}/data"
OUTPUT="${TEMP_ROOT}/process.log"
PID=""

cleanup() {
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        kill "$PID" 2>/dev/null || true
        wait "$PID" 2>/dev/null || true
    fi
    rm -rf "$TEMP_ROOT"
}
trap cleanup EXIT INT TERM

if [ ! -x "$EXECUTABLE" ]; then
    echo "ERROR: packaged executable not found: ${EXECUTABLE}" >&2
    exit 1
fi

mkdir -p "${DATA_DIR}/images"
printf 'legacy image bytes' >"${DATA_DIR}/images/existing.png"
"$PYTHON_BIN" -c '
import json, sqlite3, sys
from pathlib import Path
root = Path(sys.argv[1])
db = sqlite3.connect(root / "chat_history.db")
db.execute("CREATE TABLE conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL DEFAULT \"\", agent_id TEXT NOT NULL, created_at REAL NOT NULL)")
db.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created_at REAL NOT NULL, images TEXT NOT NULL DEFAULT \"[]\")")
db.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
db.execute("INSERT INTO conversations VALUES (1, \"升级样本\", \"custom-agent\", 1.0)")
images = [str(root / "images" / "existing.png"), str(root / "images" / "missing.png")]
db.execute("INSERT INTO messages VALUES (1, 1, \"user\", \"旧正文\", 2.0, ?)", (json.dumps(images, ensure_ascii=False),))
db.execute("INSERT INTO settings VALUES (\"custom_agents\", ?)", (json.dumps([{"id": "custom-agent", "name": "旧 Agent"}], ensure_ascii=False),))
db.execute("INSERT INTO settings VALUES (\"bad_setting\", \"{broken\")")
db.commit()
db.close()
' "$DATA_DIR"

echo "==> Launching packaged app against synthetic schema 0"
AIDE_SMOKE_TEST=1 \
AIDE_DATA_DIR="$DATA_DIR" \
AIDE_LOG_DIR="${TEMP_ROOT}/logs" \
"$EXECUTABLE" >"$OUTPUT" 2>&1 &
PID=$!

for _ in {1..40}; do
    if ! kill -0 "$PID" 2>/dev/null; then
        break
    fi
    sleep 0.25
done
if kill -0 "$PID" 2>/dev/null; then
    echo "ERROR: packaged upgrade smoke did not finish within 10 seconds" >&2
    cat "$OUTPUT" >&2
    exit 1
fi
set +e
wait "$PID"
STATUS=$?
set -e
PID=""
if [ "$STATUS" -ne 0 ]; then
    cat "$OUTPUT" >&2
    exit "$STATUS"
fi

EXPECTED_SCHEMA="$($PYTHON_BIN -c 'from ai_desktop.utils.storage import SCHEMA_VERSION; print(SCHEMA_VERSION)')"
"$PYTHON_BIN" -c '
import json, sqlite3, sys
from pathlib import Path
root, expected = Path(sys.argv[1]), int(sys.argv[2])
db = sqlite3.connect(root / "chat_history.db")
assert db.execute("PRAGMA user_version").fetchone()[0] == expected
assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
assert db.execute("SELECT title, agent_id FROM conversations").fetchone() == ("升级样本", "custom-agent")
assert db.execute("SELECT content FROM messages").fetchone()[0] == "旧正文"
assert json.loads(db.execute("SELECT value FROM settings WHERE key=\"custom_agents\"").fetchone()[0])[0]["name"] == "旧 Agent"
assert db.execute("SELECT value FROM settings WHERE key=\"bad_setting\"").fetchone()[0] == "{broken"
assert db.execute("SELECT COUNT(*) FROM attachments").fetchone()[0] == 2
db.close()
backups = list((root / "backups").glob("*.sqlite3"))
assert len(backups) == 1
backup = sqlite3.connect(backups[0])
assert backup.execute("PRAGMA user_version").fetchone()[0] == 0
assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
assert backup.execute("SELECT content FROM messages").fetchone()[0] == "旧正文"
backup.close()
' "$DATA_DIR" "$EXPECTED_SCHEMA"

echo "==> Packaged schema-0 upgrade and backup verification passed"
