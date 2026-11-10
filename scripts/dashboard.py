"""Private, loopback-only live viewer and controls for DeepSeek collaboration."""
import argparse
import hmac
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen


def read_json(path, fallback=None):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {} if fallback is None else fallback


def stable_config(root, workspace):
    path = root / 'dashboard-config.json'
    identity = hashlib.sha256(os.path.normcase(str(workspace)).encode('utf-8')).hexdigest()
    config = {'port': 43000 + int(identity[:8], 16) % 2000,
              'token': secrets.token_urlsafe(32)}
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        config = read_json(path)
    else:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(config, stream)
    if (type(config.get('port')) is not int or not 43000 <= config['port'] < 45000
            or not isinstance(config.get('token'), str) or len(config['token']) < 32):
        raise ValueError('Invalid dashboard-config.json; preserve it and repair the configuration')
    return config, identity


def health_matches(app):
    try:
        # Health probes expose no task, file, session, or webpage contents.
        with urlopen(f'http://127.0.0.1:{app.port}/health', timeout=2) as response:
            data = json.load(response)
        return (data.get('service') == 'deepseek-collab' and data.get('workspace_id') == app.identity
                and data.get('token_fingerprint') == hashlib.sha256(app.token.encode()).hexdigest())
    except (OSError, ValueError):
        return False


def launch_background(app):
    command = [sys.executable, str(Path(__file__).resolve()), '--workspace', str(app.workspace)]
    if os.name == 'nt':
        powershell = shutil.which('pwsh') or shutil.which('powershell')
        if not powershell:
            raise RuntimeError('PowerShell is required for independent background startup')
        def literal(value):
            return "'" + str(value).replace("'", "''") + "'"
        # Start-Process gives the server its own process lifetime and hides its window.
        quoted_args = ','.join(literal('"' + item + '"') for item in command[1:])
        pid_file = app.root / 'dashboard-launch.pid'
        script = ("$ErrorActionPreference='Stop'; $p=Start-Process -FilePath " + literal(command[0])
                  + ' -ArgumentList @(' + quoted_args + ') -WorkingDirectory ' + literal(app.workspace)
                  + ' -WindowStyle Hidden -RedirectStandardOutput ' + literal(app.root / 'dashboard.stdout.log')
                  + ' -RedirectStandardError ' + literal(app.root / 'dashboard.stderr.log')
                  + ' -PassThru; [System.IO.File]::WriteAllText(' + literal(pid_file) + ',[string]$p.Id)')
        # Do not capture pipes that a background Windows descendant could inherit.
        with (app.root / 'dashboard-launch.log').open('a', encoding='utf-8') as diagnostics:
            subprocess.run([powershell, '-NoProfile', '-NonInteractive', '-Command', script],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=diagnostics, check=True, timeout=15)
        return int(pid_file.read_text(encoding='utf-8').strip())
    with (app.root / 'dashboard-server.log').open('a', encoding='utf-8') as log:
        child = subprocess.Popen(command, cwd=app.workspace, stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=log, start_new_session=True, close_fds=True)
    return child.pid


class App:
    def __init__(self, workspace):
        self.workspace = workspace.resolve()
        self.root = self.workspace / 'work/deepseek-collab'
        self.runs = self.root / 'runs'
        self.runs.mkdir(parents=True, exist_ok=True)
        config, self.identity = stable_config(self.root, self.workspace)
        self.token = config['token']
        self.port = config['port']
        self.lock = threading.Lock()
        self.child = None
        self.launching = False
        self.last_error = ''

    def run_path(self, run_id):
        if not isinstance(run_id, str) or not run_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in run_id):
            raise ValueError('无效的任务编号')
        path = (self.runs / run_id).resolve()
        if path.parent != self.runs.resolve() or not path.is_dir():
            raise ValueError('任务不存在')
        return path

    def snapshot(self):
        runs = []
        for folder in sorted(self.runs.iterdir(), reverse=True)[:50]:
            if not folder.is_dir() or folder.is_symlink():
                continue
            state = read_json(folder / 'result.json') or read_json(folder / 'state.json')
            events = []
            try:
                # Bound each refresh; retain the latest events for long sessions.
                with (folder / 'events.jsonl').open('rb') as stream:
                    size = stream.seek(0, 2)
                    if size > 2_000_000:
                        stream.seek(size - 2_000_000)
                        stream.readline()
                    else:
                        stream.seek(0)
                    lines = stream.read().decode('utf-8', errors='replace').splitlines()
                for line in lines:
                    try:
                        event = json.loads(line)
                        if event.get('type') != 'thinking':
                            events.append(event)
                    except ValueError:
                        pass  # The writer may currently own an incomplete line.
            except OSError:
                pass
            try:
                task = (folder / 'request.txt').read_text(encoding='utf-8')[:100_000]
            except OSError:
                task = ''
            runs.append({'id': folder.name, 'task': task, 'state': state, 'events': events[-300:]})
        return {'workspace': str(self.workspace), 'runs': runs,
                'launching': self.launching, 'error': self.last_error}

    def stop(self, run_id):
        folder = self.run_path(run_id)
        state = read_json(folder / 'state.json')
        if (folder / 'result.json').exists() or state.get('status') != 'running':
            raise ValueError('当前任务没有在执行')
        (folder / 'stop.request').write_text('stop', encoding='utf-8')

    def revise(self, run_id, task):
        if not isinstance(task, str) or not task.strip() or len(task) > 100_000:
            raise ValueError('请填写调整要求，最多 100000 字符')
        folder = self.run_path(run_id)
        if not self.lock.acquire(blocking=False):
            raise ValueError('已有调整正在处理，请等待')
        try:
            # Do not introduce another writer while any known task is active.
            for other in self.runs.iterdir():
                if other == folder or not other.is_dir():
                    continue
                if not (other / 'result.json').exists() and read_json(other / 'state.json').get('status') == 'running':
                    raise ValueError('另一个任务仍在执行，请先停止它')
            self.launching = True
            self.last_error = ''
            threading.Thread(target=self._revise, args=(folder, task), daemon=True).start()
        except Exception:
            self.lock.release()
            raise

    def _revise(self, folder, task):
        try:
            if not (folder / 'result.json').exists():
                self.stop(folder.name)
                deadline = time.monotonic() + 25
                while not (folder / 'result.json').exists():
                    if time.monotonic() > deadline:
                        raise ValueError('未确认旧任务停止，未启动新任务。请让 Codex 检查执行状态。')
                    time.sleep(0.2)
            state = read_json(folder / 'result.json')
            session = state.get('session_id')
            if not session:
                raise ValueError('没有可续接的会话编号，请让 Codex 重新派发任务')
            import uuid
            task_file = self.root / ('revision-' + uuid.uuid4().hex + '.txt')
            task_file.write_text('用户通过协作看板调整了本任务。先检查之前已完成的修改，避免重复或覆盖用户修改。'
                                 '只在当前工作目录执行，不派生代理，不更改凭据，不发布或向他人发送消息。'
                                 '完成后报告修改和验证结果。新要求如下：\n\n' + task, encoding='utf-8')
            with (self.root / 'dashboard-dispatch.log').open('a', encoding='utf-8') as log:
                previous_runs = {p.name for p in self.runs.iterdir()}
                self.child = subprocess.Popen([sys.executable, str(Path(__file__).with_name('dispatch.py')),
                    '--workspace', str(self.workspace), '--task-file', str(task_file), '--session-id', session],
                    cwd=self.workspace, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                # Release controls once startup is visible, so this run can also be revised.
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if any(p.name not in previous_runs and (p / 'state.json').exists() for p in self.runs.iterdir()):
                        break
                    if self.child.poll() is not None:
                        raise ValueError('续接进程启动失败，请检查 dashboard-dispatch.log')
                    time.sleep(0.1)
                else:
                    raise ValueError('续接启动状态尚未确认，请刷新任务列表，不要重复提交')
        except Exception as exc:
            self.last_error = str(exc)
        finally:
            self.launching = False
            self.lock.release()


def serve(workspace):
    app = App(workspace)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, body, kind='application/json; charset=utf-8'):
            data = body.encode('utf-8') if isinstance(body, str) else json.dumps(body, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def authorized(self):
            parsed = urlsplit(self.path)
            supplied = self.headers.get('X-Dashboard-Token') or parse_qs(parsed.query).get('key', [''])[0]
            host = self.headers.get('Host', '')
            expected = f'127.0.0.1:{self.server.server_port}'
            origin = self.headers.get('Origin')
            return (host == expected and (origin is None or origin == 'http://' + expected)
                    and hmac.compare_digest(supplied, app.token))

        def do_GET(self):
            if urlsplit(self.path).path == '/health' and self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}':
                return self.reply(200, {'service': 'deepseek-collab', 'status': 'ok',
                    'workspace_id': app.identity, 'pid': os.getpid(),
                    'token_fingerprint': hashlib.sha256(app.token.encode()).hexdigest()})
            if not self.authorized():
                return self.reply(403, {'error': 'Access denied'})
            route = urlsplit(self.path).path
            if route == '/api/snapshot':
                return self.reply(200, app.snapshot())
            if route == '/':
                page = Path(__file__).with_name('dashboard.html').read_text(encoding='utf-8')
                return self.reply(200, page.replace('__TOKEN__', app.token), 'text/html; charset=utf-8')
            self.reply(404, {'error': 'Not found'})

        def do_POST(self):
            if not self.authorized():
                return self.reply(403, {'error': 'Access denied'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if size <= 0 or size > 500_000:
                    raise ValueError('请求过大或为空')
                payload = json.loads(self.rfile.read(size))
                route = urlsplit(self.path).path
                if route == '/api/stop':
                    app.stop(payload.get('run_id'))
                elif route == '/api/revise':
                    app.revise(payload.get('run_id'), payload.get('task'))
                elif route == '/api/delete':
                    app.delete(payload.get('run_id'))
                elif route == '/api/restore':
                    app.restore(payload.get('run_id'))
                else:
                    return self.reply(404, {'error': 'Not found'})
                self.reply(200, {'ok': True})
            except (ValueError, TypeError, AttributeError) as exc:
                self.reply(400, {'error': str(exc)})

    try:
        server = ThreadingHTTPServer(('127.0.0.1', app.port), Handler)
    except OSError as exc:
        raise RuntimeError(f'Fixed dashboard port {app.port} is occupied. No random port was selected; inspect the listener.') from exc
    url = f'http://127.0.0.1:{server.server_port}/?key={app.token}'
    (app.root / 'dashboard.json').write_text(json.dumps({'url': url, 'pid': os.getpid()}), encoding='utf-8')
    print(json.dumps({'url': url}, ensure_ascii=False), flush=True)
    server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--start', action='store_true', help='start detached or return an existing viewer URL')
    args = parser.parse_args()
    workspace = args.workspace.resolve(strict=True)
    if not args.start:
        return serve(workspace)
    app = App(workspace)
    info = app.root / 'dashboard.json'
    if health_matches(app):
        print(json.dumps({'url': f'http://127.0.0.1:{app.port}/?key={app.token}',
                          'status': 'reused'}), flush=True)
        return
    child_pid = launch_background(app)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        current = read_json(info)
        if current.get('pid') == child_pid and health_matches(app):
            current['status'] = 'started'
            print(json.dumps(current), flush=True)
            return
        time.sleep(0.1)
    raise RuntimeError('Viewer startup timed out; inspect dashboard.stderr.log. The fixed entry was preserved.')


if __name__ == '__main__':
    main()
