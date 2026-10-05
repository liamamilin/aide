"""Controller-thread audit storage. Payloads expire; identity metadata does not.

No raw files or HTTP diagnostics are written. Model input is never loaded from
these tables. Managed backup cleanup is retried through durable deletion IDs.
"""
import json
import logging
import re
import sqlite3
import time
from dataclasses import asdict

from ai_desktop.llm.run_types import RunEventKind
from ai_desktop.utils import storage

logger = logging.getLogger(__name__)
RETENTION_SECONDS = 30 * 24 * 3600
MAX_PAYLOAD_BYTES = 65536
MASK = '[REDACTED]'
_SECRET_KEY = (r'(?:api[_-]?key|token|password|passwd|secret|authorization|'
               r'cookie|credential|private[_-]?key)')
_KEY = re.compile(_SECRET_KEY, re.I)
_ASSIGN = re.compile(r'(?i)([\w.-]*' + _SECRET_KEY + r'[\w.-]*["\']?\s*[:=]\s*)(?:"[^"]*"|\'[^\']*\'|[^\s,;}&]+)')
_BEARER = re.compile(r'(?i)\bBearer\s+[^\s"\'<>]+')
_TOKEN = re.compile(r'\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{16,}|github_pat_[A-Za-z0-9_]{16,})\b')
_PEM = re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)', re.S)
_USERINFO = re.compile(r'(https?://)[^\s/<>]+@', re.I)
_CLOSED = frozenset({'succeeded', 'failed', 'cancelled', 'limited', 'interrupted'})


def redact_text(value):
    value = _PEM.sub(MASK, value)
    value = _USERINFO.sub(r'\1' + MASK + '@', value)
    value = _BEARER.sub('Bearer ' + MASK, value)
    value = _TOKEN.sub(MASK, value)
    return _ASSIGN.sub(lambda m: m[1] + MASK, value)


def redact(value, depth=0):
    if depth > 20:
        return '[nested data omitted]'
    if isinstance(value, dict):
        return {str(key): MASK if _KEY.search(str(key)) else redact(item, depth+1)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item, depth+1) for item in value]
    if isinstance(value, str):
        # Tool output/arguments can contain JSON inside JSON. Preserve its
        # protocol while redacting nested keys before serializing the audit copy.
        try:
            nested = json.loads(value) if value.startswith(('{', '[')) else None
        except (ValueError, RecursionError):
            nested = None
        if isinstance(nested, (dict, list)):
            return json.dumps(redact(nested, depth+1), ensure_ascii=False, separators=(',', ':'))
        return redact_text(value)
    return value if value is None or isinstance(value, (bool, int, float)) else '[unsupported]'


def encode_payload(value):
    text = json.dumps(redact(value), ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    if len(text.encode()) <= MAX_PAYLOAD_BYTES:
        return text
    preview = text.encode()[:MAX_PAYLOAD_BYTES // 2].decode('utf-8', errors='ignore')
    while True:
        result = json.dumps({'truncated': True, 'preview': preview}, ensure_ascii=False)
        if len(result.encode()) <= MAX_PAYLOAD_BYTES:
            return result
        preview = preview[:len(preview) // 2]


def migrate(db):
    db.execute('''CREATE TABLE agent_runs (
        run_id TEXT PRIMARY KEY, conversation_id INTEGER NOT NULL, user_message_id INTEGER NOT NULL,
        generation_id INTEGER, agent_id TEXT NOT NULL, origin TEXT NOT NULL, action_id TEXT,
        config_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'running',
        started_at REAL NOT NULL, ended_at REAL, updated_at REAL NOT NULL,
        error_code TEXT NOT NULL DEFAULT '', last_seq INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
        FOREIGN KEY(user_message_id) REFERENCES messages(id) ON DELETE CASCADE,
        FOREIGN KEY(generation_id) REFERENCES generations(id) ON DELETE SET NULL)''')
    db.execute('''CREATE TABLE agent_steps (
        id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, step_index INTEGER NOT NULL,
        step_id TEXT NOT NULL, request_id TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('model','tool')),
        tool_call_id TEXT NOT NULL DEFAULT '', provider_call_id TEXT, tool_name TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL, started_at REAL NOT NULL, ended_at REAL,
        metadata_json TEXT NOT NULL DEFAULT '{}', payload_json TEXT, payload_expires_at REAL,
        payload_purged_at REAL,
        UNIQUE(run_id, request_id, kind, tool_call_id), UNIQUE(run_id, step_index),
        FOREIGN KEY(run_id) REFERENCES agent_runs(run_id) ON DELETE CASCADE)''')
    db.execute('CREATE INDEX idx_agent_runs_conversation ON agent_runs(conversation_id, started_at, run_id)')
    db.execute('CREATE INDEX idx_agent_runs_generation ON agent_runs(generation_id)')
    db.execute('CREATE INDEX idx_agent_steps_expiry ON agent_steps(payload_expires_at)')
    db.execute('CREATE TABLE audit_deletions(run_id TEXT PRIMARY KEY, deleted_at REAL NOT NULL)')


def begin_run(context, user_message_id, *, admission=None):
    request = context.request
    db = storage._conn()
    row = db.execute('SELECT conversation_id, role FROM messages WHERE id=?', (user_message_id,)).fetchone()
    if row is None or row['conversation_id'] != request.conversation_id or row['role'] != 'user':
        raise ValueError('Audit run must belong to its user message')
    snapshot = {'model': request.model, 'think': request.think, 'think_setting': request.think_setting.record(),
                'think_source': request.think_source,
                'options': dict(request.options), 'allowed_tools': [tool.name for tool in context.tools],
                'limits': asdict(context.limits),
                'execution': context.execution.record() if context.execution else None,
                'search': context.search_settings.record() if context.search_settings else None}
    if admission is not None:
        snapshot['admission'] = admission.record()
    now = time.time()
    with db:
        db.execute('''INSERT INTO agent_runs(run_id, conversation_id, user_message_id, agent_id, origin,
                      action_id, config_json, started_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)''',
                   (request.run_id, request.conversation_id, user_message_id, request.agent_id, request.origin,
                    request.action_id, encode_payload(snapshot), now, now))


def _finish(db, run_id, status, now, error_code=''):
    if status not in _CLOSED:
        raise ValueError('Invalid audit terminal status')
    db.execute('''UPDATE agent_runs SET status=?, ended_at=?, updated_at=?, error_code=?
                  WHERE run_id=? AND ended_at IS NULL''', (status, now, now, error_code, run_id))
    db.execute('''UPDATE agent_steps SET status=?, ended_at=?, payload_expires_at=?
                  WHERE run_id=? AND ended_at IS NULL''',
               ('cancelled' if status == 'cancelled' else 'interrupted', now, now+RETENTION_SECONDS, run_id))


def finish_run(run_id, status, error_code=''):
    with storage._conn() as db:
        _finish(db, run_id, status, time.time(), error_code)


def observe(event):
    if event.kind in {RunEventKind.CONTENT, RunEventKind.THINKING}:
        return False  # Never write per-token content.
    db = storage._conn()
    run = db.execute('SELECT * FROM agent_runs WHERE run_id=?', (event.run_id,)).fetchone()
    if (run is None or run['ended_at'] is not None or run['conversation_id'] != event.conversation_id
            or event.seq <= run['last_seq']):
        return False
    now = time.time()
    key = (event.run_id, event.request_id, 'tool' if event.local_call_id else 'model', event.local_call_id or '')
    payload = json.loads(event.payload_json) if event.payload_json else {}
    with db:
        if event.kind in {RunEventKind.MODEL_STARTED, RunEventKind.TOOL_STARTED}:
            if event.kind == RunEventKind.TOOL_STARTED:
                parent = db.execute('SELECT 1 FROM agent_steps WHERE run_id=? AND step_id=? AND request_id=? '
                                    'AND kind="model"', (event.run_id, event.step_id, event.request_id)).fetchone()
                if parent is None:
                    return False
                payload = {'arguments_json': event.arguments_json}
            db.execute('''INSERT OR IGNORE INTO agent_steps(run_id, step_index, step_id, request_id, kind,
                          tool_call_id, provider_call_id, tool_name, status, started_at, payload_json)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                       (event.run_id, db.execute('SELECT COALESCE(MAX(step_index),0)+1 FROM agent_steps '
                                                'WHERE run_id=?', (event.run_id,)).fetchone()[0],
                        event.step_id, event.request_id, key[2], key[3],
                        redact_text(event.provider_call_id) if event.provider_call_id else None,
                        event.tool_name if event.tool_name in {'bash', 'web_search'} else '',
                        'running', now, encode_payload(payload)))
        elif event.kind in {RunEventKind.MODEL_FINISHED, RunEventKind.TOOL_FINISHED, RunEventKind.TOOL_UPDATED}:
            step = db.execute('''SELECT * FROM agent_steps WHERE run_id=? AND request_id=? AND kind=?
                                 AND tool_call_id=?''', key).fetchone()
            if step is None or step['ended_at'] is not None or step['step_id'] != event.step_id:
                return False
            if event.kind == RunEventKind.TOOL_UPDATED:
                states = {'waiting_confirmation', 'executing', 'searching'}
                state = event.status if event.status in states else 'running'
                db.execute('UPDATE agent_steps SET status=? WHERE id=?', (state, step['id']))
            else:
                original = json.loads(step['payload_json'] or '{}')
                if event.output is not None:
                    original['output'] = event.output.text
                    meta = {'error': event.output.error}
                    try:
                        output = json.loads(event.output.text)
                    except ValueError:
                        output = {}
                    if isinstance(output, dict):
                        fields = ('exit_code', 'duration', 'truncated', 'error_type', 'provider')
                        meta.update({k: output[k] for k in fields if k in output})
                else:
                    original.update(payload)
                    meta = {k: payload.get('output', {}).get(k) for k in ('done_reason', 'error_code')}
                status = ('limited' if event.kind == RunEventKind.MODEL_FINISHED
                          and meta.get('done_reason') == 'length' else event.status)
                db.execute('''UPDATE agent_steps SET status=?, ended_at=?, metadata_json=?, payload_json=?,
                              payload_expires_at=? WHERE id=?''',
                           (status, now, encode_payload(meta), encode_payload(original), now+RETENTION_SECONDS,
                            step['id']))
        elif event.kind == RunEventKind.FINISHED:
            _finish(db, event.run_id, event.status, now, payload.get('error_code', ''))
        db.execute('UPDATE agent_runs SET last_seq=?, updated_at=? WHERE run_id=?', (event.seq, now, event.run_id))
    return True


def link_generation(run_id, generation_id):
    db = storage._conn()
    with db:
        db.execute('''UPDATE agent_runs SET generation_id=? WHERE run_id=? AND user_message_id=
                      (SELECT user_message_id FROM generations WHERE id=?)''', (generation_id, run_id, generation_id))


def recover_interrupted(db, *, snapshot=False, now=None):
    now = time.time() if now is None else now
    with db:
        rows = db.execute('SELECT run_id, updated_at FROM agent_runs WHERE ended_at IS NULL').fetchall()
        for row in rows:
            # A frozen backup has no live worker. Use its last recorded time,
            # so repeatedly visiting a backup cannot extend its retention.
            _finish(db, row['run_id'], 'interrupted', row['updated_at'] if snapshot else now)


def queue_deletions(db, column, identity):
    if column not in {'conversation_id', 'user_message_id'}:
        raise ValueError('Unknown deletion scope')
    db.execute(f'''INSERT OR IGNORE INTO audit_deletions SELECT run_id, ? FROM agent_runs WHERE {column}=?''',
               (time.time(), identity))


def _purge(db, now):
    result = db.execute('''UPDATE agent_steps SET payload_json=NULL, payload_purged_at=?
                           WHERE payload_purged_at IS NULL AND payload_expires_at<=?
                           AND run_id IN (SELECT run_id FROM agent_runs WHERE ended_at IS NOT NULL)''', (now, now))
    return result.rowcount


def cleanup(*, now=None):
    now = time.time() if now is None else now
    db = storage._conn()
    with db:
        count = _purge(db, now)
    deletions = [row[0] for row in db.execute('SELECT run_id FROM audit_deletions')]
    pending = False
    directory = storage.DB_PATH.parent/'backups'
    for path in directory.glob(f'{storage.DB_PATH.stem}.*.sqlite3'):
        if path.is_symlink() or not ('.schema-' in path.name or '.before-restore.' in path.name):
            continue
        backup = None
        try:
            # No arbitrary user backups or caller-supplied paths are modified.
            backup = sqlite3.connect(str(path), timeout=.1)
            backup.row_factory = sqlite3.Row
            backup.execute('PRAGMA foreign_keys=ON')
            tables = {row[0] for row in backup.execute('SELECT name FROM sqlite_master WHERE type="table"')}
            if not {'agent_runs', 'agent_steps'} <= tables:
                continue
            with backup:
                recover_interrupted(backup, snapshot=True, now=now)
                _purge(backup, now)
                backup.executemany('DELETE FROM agent_runs WHERE run_id=?', [(run,) for run in deletions])
        except (OSError, sqlite3.Error):
            pending = True
            logger.warning('Managed audit backup cleanup deferred')
        finally:
            if backup is not None:
                backup.close()
    if not pending and deletions:
        with db:
            db.executemany('DELETE FROM audit_deletions WHERE run_id=?', [(run,) for run in deletions])
    return count


def list_runs(conversation_id):
    cleanup()
    db = storage._conn()
    runs = [dict(row) for row in db.execute('SELECT * FROM agent_runs WHERE conversation_id=? '
                                           'ORDER BY started_at DESC, run_id', (conversation_id,))]
    for run in runs:
        run['config'] = json.loads(run.pop('config_json'))
        run['steps'] = []
        for row in db.execute('SELECT * FROM agent_steps WHERE run_id=? ORDER BY step_index', (run['run_id'],)):
            step = dict(row)
            step['metadata'] = json.loads(step.pop('metadata_json'))
            raw = step.pop('payload_json')
            step['payload'] = json.loads(raw) if raw is not None else None
            run['steps'].append(step)
    return runs


def sources_for_message(message_id=None, *, generation_id=None, cleanup_first=True):
    if cleanup_first:
        cleanup()
    db = storage._conn()
    if generation_id is None:
        row = db.execute('SELECT id FROM generations WHERE assistant_message_id=? AND status="succeeded" '
                         'ORDER BY active DESC, id DESC LIMIT 1', (message_id,)).fetchone()
        if row is None:
            return {}
        generation_id = row[0]
    rows = db.execute('''SELECT s.payload_json, s.payload_expires_at FROM agent_steps s
                        JOIN agent_runs r ON r.run_id=s.run_id
                        WHERE r.generation_id=? AND s.tool_name='web_search' AND s.status='succeeded'
                        AND s.payload_purged_at IS NULL ORDER BY s.step_index''', (generation_id,))
    # Lazy import avoids pulling GUI widgets into database migration/CLI tools.
    from ai_desktop.services.web_search import safe_source_url
    sources = {}
    for row in rows:
        try:
            record = json.loads(json.loads(row[0])['output'])
            values = record['sources']
        except (ValueError, TypeError, KeyError):
            continue
        if not isinstance(values, list):
            continue
        for source in values[:5]:
            if (isinstance(source, dict) and re.fullmatch(r'S(?:[1-9]|[12][0-9]|30)', str(source.get('source_id', '')))
                    and safe_source_url(source.get('url')) and MASK not in source['url']
                    and isinstance(source.get('title'), str)):
                sources.setdefault(source['source_id'], {**source, 'expires_at': row['payload_expires_at']})
    return sources


def export_markdown(conversation_id):
    runs = list_runs(conversation_id)
    if not runs:
        return ''
    lines = ['\n## 运行记录（审计内容已脱敏）\n']
    for run in reversed(runs):
        lines.append(f'\n### {run["run_id"]}\n\n状态：{run["status"]} · 来源：{run["origin"]}\n')
        for step in run['steps']:
            lines.append(f'\n{step["step_index"]}. {step["tool_name"] or "model"} · {step["status"]}\n')
            if step['payload'] is None:
                lines.append('\n执行详情已过期。\n')
            else:
                # Indented code cannot escape through payload-provided fences.
                text = json.dumps(step['payload'], ensure_ascii=False, indent=2)
                lines.extend('    '+line+'\n' for line in text.splitlines())
    return ''.join(lines)
