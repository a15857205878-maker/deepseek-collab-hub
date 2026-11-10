"""Unified dashboard, using original-workspace controls and a separate archive."""
from pathlib import Path
import sys
import dashboard
from store import Store, home

ProjectApp = dashboard.App


class HubApp(ProjectApp):
    def __init__(self, workspace):
        super().__init__(workspace)
        self.store = Store(workspace)
        self.project_apps = {}

    def project_app(self, run_id):
        record = self.store.get(run_id)
        if record['deleted']:
            raise ValueError('请先恢复任务记录，再进行执行操作')
        if not (Path(record['workspace']) / 'work/deepseek-collab/runs' / record['local_id']).is_dir():
            raise ValueError('原工作区或会话不可用；归档仍可查看，请让 Codex 检查')
        workspace = record['workspace']
        if workspace not in self.project_apps:
            self.project_apps[workspace] = ProjectApp(Path(workspace))
        return self.project_apps[workspace], record

    def snapshot(self):
        self.store.refresh()
        runs = self.store.search(include_events=True, include_deleted=True)
        for run in runs:
            run['source_available'] = (Path(run['workspace']) / 'work/deepseek-collab/runs' / run['local_id']).is_dir()
            run['can_resume'] = run['source_available'] and bool(run['state'].get('session_id')) and not run['deleted']
            run['state']['session_id'] = (run['project_id'] + ':' + run['state']['session_id']) if run['state'].get('session_id') else None
        return {'workspace': str(self.store.root), 'projects': self.store.projects(), 'runs': runs,
                'launching': any(app.launching for app in self.project_apps.values()),
                'error': next((app.last_error for app in self.project_apps.values() if app.last_error), '')}

    def stop(self, run_id):
        app, record = self.project_app(run_id)
        app.stop(record['local_id'])

    def revise(self, run_id, task):
        app, record = self.project_app(run_id)
        if not record['resumable']:
            raise ValueError('该任务没有可续接的原会话，请让 Codex 检查')
        app.revise(record['local_id'], task)

    def delete(self, run_id):
        if any(app.launching for app in self.project_apps.values()):
            raise ValueError('正在续接任务，请稍后再删除')
        self.store.refresh()
        return self.store.trash(run_id)

    def restore(self, run_id):
        return self.store.restore(run_id)


if __name__ == '__main__':
    # Keep the existing fixed-port startup, host/origin/token checks and controls.
    dashboard.App = HubApp
    dashboard.__file__ = __file__
    if '--workspace' not in sys.argv:
        sys.argv.extend(['--workspace', str(home())])
    home().mkdir(parents=True, exist_ok=True)
    dashboard.main()
