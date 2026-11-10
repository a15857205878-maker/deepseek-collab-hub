"""Local registry and durable task archive. Never scans unregistered workspaces."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import uuid
from contextlib import contextmanager

LOCK = threading.RLock()


def home():
    return Path(os.environ.get('DEEPSEEK_HUB_HOME', str(Path.home() / 'Documents/Codex/DeepSeek-Collab-Data'))).resolve()


def read_json(path, fallback=None):
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return {} if fallback is None else fallback


class Store:
    def __init__(self, root=None):
        self.root = Path(root or home()).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, path TEXT UNIQUE, name TEXT);
                CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, project_id TEXT, local_id TEXT,
                    task TEXT, state TEXT, events TEXT, signature TEXT);
                CREATE INDEX IF NOT EXISTS runs_project ON runs(project_id);
                CREATE TABLE IF NOT EXISTS deleted_groups (group_key TEXT PRIMARY KEY);
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.root / 'index.sqlite3', timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def projects(self):
        with self.db() as db:
            return [dict(row) for row in db.execute('SELECT * FROM projects ORDER BY name')]

    def register(self, workspace, name=None):
        path = Path(workspace).resolve(strict=True)
        if not path.is_dir():
            raise ValueError('工作区必须是现有目录')
        key = hashlib.sha256(os.path.normcase(str(path)).encode()).hexdigest()[:20]
        with LOCK, self.db() as db:
            old = db.execute('SELECT name FROM projects WHERE id=?', (key,)).fetchone()
            label = name or (old['name'] if old else path.name)
            db.execute('INSERT OR REPLACE INTO projects VALUES (?,?,?)', (key, str(path), label))
        return {'id': key, 'path': str(path), 'name': label}

    def refresh(self):
        with LOCK:
            for project in self.projects():
                root = Path(project['path']) / 'work/deepseek-collab/runs'
                if not root.is_dir() or root.is_symlink():
                    continue
                for folder in root.iterdir():
                    if not folder.is_dir() or folder.is_symlink() or not all(c.isascii() and (c.isalnum() or c in '-_') for c in folder.name):
                        continue
                    if folder.resolve().parent != root.resolve():
                        continue
                    paths = [folder / n for n in ('request.txt', 'state.json', 'result.json', 'events.jsonl')]
                    if any(p.is_symlink() for p in paths):
                        continue
                    sig = []
                    for path in paths:
                        try:
                            stat = path.stat()
                            sig.append((stat.st_mtime_ns, stat.st_size))
                        except OSError:
                            sig.append(None)
                    signature = json.dumps(sig)
                    key = project['id'] + '__' + folder.name
                    with self.db() as db:
                        old = db.execute('SELECT signature FROM runs WHERE id=?', (key,)).fetchone()
                    if old and old['signature'] == signature:
                        continue
                    state = read_json(folder / 'result.json') or read_json(folder / 'state.json')
                    if not state:
                        continue
                    try:
                        task = (folder / 'request.txt').read_text(encoding='utf-8-sig')[:100000]
                    except OSError:
                        task = ''
                    events = []
                    try:
                        with (folder / 'events.jsonl').open(encoding='utf-8', errors='replace') as stream:
                            for line in stream:
                                try:
                                    event = json.loads(line)
                                    if isinstance(event, dict) and event.get('type') != 'thinking':
                                        events.append(event)
                                except ValueError:
                                    pass
                    except OSError:
                        pass
                    state_text = json.dumps(state, ensure_ascii=False)
                    events_text = json.dumps(events, ensure_ascii=False)
                    archive = self.root / 'projects' / project['id'] / 'runs' / folder.name
                    archive.mkdir(parents=True, exist_ok=True)
                    for name, text in [('request.txt', task), ('state.json', state_text), ('events.jsonl', '\n'.join(json.dumps(e, ensure_ascii=False) for e in events))]:
                        temporary = archive / (name + '.' + uuid.uuid4().hex + '.tmp')
                        temporary.write_text(text, encoding='utf-8')
                        temporary.replace(archive / name)
                    with self.db() as db:
                        db.execute('INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?)', (key, project['id'], folder.name, task, state_text, events_text, signature))

    def search(self, query='', project_id='', status='', limit=200, offset=0, include_events=False, include_deleted=False, deleted_only=False):
        if not isinstance(limit, int) or not 1 <= limit <= 1000 or not isinstance(offset, int) or offset < 0:
            raise ValueError('limit 范围为 1–1000，offset 必须非负')
        deleted_sql = "EXISTS(SELECT 1 FROM deleted_groups d WHERE d.group_key=r.project_id||':'||coalesce(nullif(json_extract(r.state,'$.session_id'),''),r.local_id))"
        sql = 'SELECT r.*,p.path AS workspace,p.name AS project_name, '+deleted_sql+' AS deleted FROM runs r JOIN projects p ON p.id=r.project_id WHERE 1=1'
        args = []
        if deleted_only:
            sql += ' AND ' + deleted_sql
        elif not include_deleted:
            sql += ' AND NOT ' + deleted_sql
        if project_id:
            sql += ' AND r.project_id=?'
            args.append(project_id)
        if query:
            sql += ' AND (instr(lower(r.task),lower(?))>0 OR instr(lower(p.name),lower(?))>0)'
            args += [query, query]
        if status:
            sql += " AND json_extract(r.state,'$.status')=?"
            args.append(status)
        sql += ' ORDER BY r.local_id DESC,r.id DESC LIMIT ? OFFSET ?'
        args += [limit, offset]
        with self.db() as db:
            rows = db.execute(sql, args).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item['state'] = json.loads(item['state'])
            item['deleted'] = bool(item['deleted'])
            item['event_count'] = len(json.loads(item['events']))
            item['events'] = json.loads(item['events'])[-300:] if include_events else []
            item.pop('signature')
            result.append(item)
        return result

    def get(self, run_id):
        with self.db() as db:
            row = db.execute('SELECT r.*,p.path AS workspace,p.name AS project_name FROM runs r JOIN projects p ON p.id=r.project_id WHERE r.id=?', (run_id,)).fetchone()
        if row is None:
            raise ValueError('任务记录不存在')
        item = dict(row)
        item['state'] = json.loads(item['state'])
        item['events'] = json.loads(item['events'])
        item.pop('signature')
        item['resumable'] = bool(item['state'].get('session_id')) and (Path(item['workspace']) / 'work/deepseek-collab/runs' / item['local_id']).is_dir()
        with self.db() as db:
            item['deleted'] = db.execute('SELECT 1 FROM deleted_groups WHERE group_key=?', (self.group_key(item),)).fetchone() is not None
        if item['deleted']:
            item['resumable'] = False
        return item

    @staticmethod
    def group_key(record):
        return record['project_id'] + ':' + (record['state'].get('session_id') or record['local_id'])

    def trash(self, run_id):
        with LOCK:
            record = self.get(run_id)
            key = self.group_key(record)
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                rows = db.execute('SELECT project_id,local_id,state FROM runs WHERE project_id=?', (record['project_id'],)).fetchall()
                for row in rows:
                    item = dict(row)
                    item['state'] = json.loads(item['state'])
                    if self.group_key(item) == key and item['state'].get('status') == 'running':
                        raise ValueError('任务仍在执行，请先停止后再删除记录')
                db.execute('INSERT OR IGNORE INTO deleted_groups VALUES (?)', (key,))
        return {'ok': True, 'message': '整个任务及其历史轮次已移到已删除，可随时恢复'}

    def restore(self, run_id):
        with LOCK:
            record = self.get(run_id)
            with self.db() as db:
                db.execute('DELETE FROM deleted_groups WHERE group_key=?', (self.group_key(record),))
        return {'ok': True, 'message': '任务记录已恢复'}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--register')
    parser.add_argument('--name')
    parser.add_argument('--query', default='')
    args = parser.parse_args()
    store = Store()
    if args.register:
        store.register(args.register, args.name)
    store.refresh()
    print(json.dumps({'projects': store.projects(), 'runs': [{k: r[k] for k in ('id', 'project_name')} for r in store.search(args.query)]}, ensure_ascii=False))
