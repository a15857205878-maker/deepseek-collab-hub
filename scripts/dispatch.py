"""Dispatch one task to the installed DeepSeek Harness, without shell interpolation."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid


def write_state_atomic(target, payload):
    """A transient Windows dashboard reader must never kill the event consumer."""
    temporary = target.with_name('state.tmp')
    temporary.write_text(payload, encoding='utf-8')
    for attempt in range(20):
        try:
            temporary.replace(target)
            return True
        except PermissionError:
            if attempt == 19:
                return False
            time.sleep(0.025)


def launch_command():
    configured = os.environ.get('DEEPSEEK_HARNESS_HOME')
    local = os.environ.get('LOCALAPPDATA')
    if not configured and not local:
        raise FileNotFoundError('Set DEEPSEEK_HARNESS_HOME to a Windows DeepSeek Harness installation directory')
    app = Path(configured).expanduser() if configured else Path(local) / 'Programs' / 'DeepSeek Harness'
    exe = app / 'DeepSeek Harness.exe'
    entry = app / 'resources/app.asar/dsh/node_modules/@deepseek-ai/dsh-desktop-host/lib/cli.js'
    if not exe.is_file() or not (app / 'resources/app.asar').is_file():
        raise FileNotFoundError('DeepSeek Harness installation not found; install/configure Harness separately or set DEEPSEEK_HARNESS_HOME')
    return [str(exe), '--expose-internals', str(entry), 'headless', '--patch',
            str(Path(__file__).with_name('headless.patch.yml')), '--json']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight', action='store_true', help='Check installation paths without starting Harness or reading credentials')
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--task-file', type=Path)
    parser.add_argument('--session-id')
    parser.add_argument('--timeout', type=int, default=1200)
    args = parser.parse_args()
    if args.preflight:
        launch_command()
        print(json.dumps({'status': 'ready', 'launched': False}))
        return 0
    if args.workspace is None or args.task_file is None:
        parser.error('--workspace and --task-file are required unless --preflight is used')
    # Fail before touching registry or project data when Harness is missing.
    command = launch_command()
    workspace = args.workspace.resolve(strict=True)
    if not workspace.is_dir() or args.timeout <= 0:
        parser.error('workspace must be a directory and timeout must be positive')
    from store import Store
    Store().register(workspace)
    task = args.task_file.read_text(encoding='utf-8-sig')
    if not task.strip():
        parser.error('task file must not be empty')
    if args.session_id:
        command += ['--session-id', args.session_id]
    command += ['-']
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    run_dir = workspace / 'work/deepseek-collab/runs' / (stamp + '-' + uuid.uuid4().hex[:8])
    run_dir.mkdir(parents=True)
    (run_dir / 'request.txt').write_text(task, encoding='utf-8')
    env = os.environ.copy()
    env['ELECTRON_RUN_AS_NODE'] = '1'
    state = {'session_id': args.session_id, 'workspace': str(workspace),
             'run_dir': str(run_dir), 'status': 'running', 'answer': '', 'pid': os.getpid()}
    state_lock = threading.RLock()

    def save_state():
        with state_lock:
            state['updated_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
            target = run_dir / 'state.json'
            if not write_state_atomic(target, json.dumps(state, ensure_ascii=False)):
                state['dashboard_warning'] = 'Dashboard state file busy; execution and event capture continue.'

    save_state()
    errors = []
    terminal = None
    final_seen = False
    reader_errors = []
    with (run_dir / 'stderr.log').open('w', encoding='utf-8') as diagnostics:
        process = subprocess.Popen(command, cwd=workspace, env=env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=diagnostics,
                                   text=True, encoding='utf-8', errors='replace')

        def consume():
            nonlocal terminal, final_seen
            try:
                with (run_dir / 'events.jsonl').open('w', encoding='utf-8') as events:
                    for line in process.stdout:
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            errors.append('Harness emitted a non-JSON output line; see stderr.log')
                            continue
                        # Do not persist private reasoning streams.
                        if event.get('type') == 'thinking':
                            continue
                        events.write(json.dumps(event, ensure_ascii=False) + '\n')
                        events.flush()
                        if event.get('type') == 'session':
                            state['session_id'] = event.get('sessionId')
                            print(json.dumps({'status': 'started', 'session_id': state['session_id'],
                                              'run_dir': str(run_dir)}, ensure_ascii=False), flush=True)
                        elif event.get('type') == 'final':
                            final_seen = True
                            state['answer'] = event.get('text', '')
                        elif event.get('type') == 'error':
                            errors.append(event.get('message', 'Harness error'))
                        elif event.get('type') == 'status' and event.get('phase') == 'turn_end':
                            terminal = event.get('reason', {})
                        with state_lock:
                            if event.get('type') == 'status':
                                state['phase'] = event.get('phase')
                            save_state()
            except Exception as exc:
                reader_errors.append(str(exc))

        reader = threading.Thread(target=consume, daemon=True)
        reader.start()
        timed_out = False
        stopped = False
        try:
            process.stdin.write(task)
            process.stdin.close()
            deadline = time.monotonic() + args.timeout
            while process.poll() is None:
                stopped = (run_dir / 'stop.request').exists()
                timed_out = time.monotonic() >= deadline
                if stopped or timed_out:
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                   capture_output=True, check=False)
                    process.wait(timeout=15)
                    break
                time.sleep(0.25)
        except BrokenPipeError:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            timed_out = True
            # Terminate only the process tree created for this invocation.
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           capture_output=True, check=False)
            process.wait(timeout=15)
        finally:
            reader.join(timeout=15)
            process.stdout.close()
    if reader.is_alive():
        reader_errors.append('Output reader did not finish')
    errors.extend(reader_errors)
    state['exit_code'] = process.returncode
    state['terminal'] = terminal
    state['status'] = ('stopped' if stopped else 'timeout' if timed_out else 'completed' if
                       process.returncode == 0 and final_seen and
                       (terminal or {}).get('kind') == 'completed' and not errors else 'failed')
    state['errors'] = errors
    save_state()
    (run_dir / 'result.json').write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    (run_dir / 'answer.md').write_text(state['answer'], encoding='utf-8')
    print(json.dumps(state, ensure_ascii=False), flush=True)
    return 0 if state['status'] == 'completed' else 1


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({'status': 'failed', 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
