"""Offline release tests: synthetic data only, no model or Harness process."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import dispatch
import package
from store import Store

class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.env = os.environ.copy()
        self.env['DEEPSEEK_HUB_HOME'] = str(self.base / 'private-data')
        self.env['DEEPSEEK_HARNESS_HOME'] = str(self.base / 'missing-harness')
        self.env['PYTHONDONTWRITEBYTECODE'] = '1'

    def test_relocated_mcp_stdio_empty_state(self):
        relocated = self.base / 'relocated plugin 中文'
        shutil.copytree(ROOT, relocated, ignore=shutil.ignore_patterns('__pycache__'))
        requests = [
            {'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18'}},
            {'jsonrpc':'2.0','id':2,'method':'tools/list'},
            {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'list_projects','arguments':{}}},
            {'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'search_tasks','arguments':{}}}]
        result = subprocess.run([sys.executable,str(relocated/'scripts/server.py')],
            input=''.join(json.dumps(r)+'\n' for r in requests), text=True, encoding='utf-8',
            capture_output=True, cwd=self.base, env=self.env, timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(replies[0]['result']['serverInfo']['version'],'0.3.1')
        self.assertEqual(len(replies[1]['result']['tools']),5)
        self.assertEqual(json.loads(replies[2]['result']['content'][0]['text'])['projects'],[])
        self.assertEqual(json.loads(replies[3]['result']['content'][0]['text'])['runs'],[])

    def test_fake_archive_filters_reasoning_and_restores_group(self):
        workspace=self.base/'fake-project';run=workspace/'work/deepseek-collab/runs/fake-001'
        run.mkdir(parents=True)
        (run/'request.txt').write_text('synthetic release test',encoding='utf-8')
        (run/'state.json').write_text(json.dumps({'status':'completed','session_id':'fake-session'}),encoding='utf-8')
        (run/'events.jsonl').write_text(json.dumps({'type':'thinking','text':'PRIVATE_TEST_SENTINEL'})+'\n'+json.dumps({'type':'final','text':'public synthetic answer'}),encoding='utf-8')
        store=Store(self.base/'archive');store.register(workspace);store.refresh()
        records=store.search('synthetic');self.assertEqual(len(records),1)
        key=records[0]['id'];record=store.get(key)
        self.assertEqual(record['workspace'],str(workspace.resolve()))
        self.assertEqual(len(record['events']),1);self.assertTrue(record['resumable'])
        self.assertNotIn('PRIVATE_TEST_SENTINEL',json.dumps(record))
        archive_events=list((self.base/'archive/projects').rglob('events.jsonl'))[0]
        self.assertNotIn('PRIVATE_TEST_SENTINEL',archive_events.read_text())
        store.trash(key);self.assertEqual(store.search(),[]);self.assertFalse(store.get(key)['resumable'])
        store.restore(key);self.assertEqual(len(store.search()),1)
        with self.assertRaises(ValueError):store.search(limit=0)

    def test_missing_harness_preflight_has_no_side_effects(self):
        result=subprocess.run([sys.executable,str(ROOT/'scripts/dispatch.py'),'--preflight'],
            capture_output=True,text=True,encoding='utf-8',env=self.env,timeout=15)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('DEEPSEEK_HARNESS_HOME',result.stderr)
        self.assertFalse((self.base/'private-data').exists())

    def test_custom_harness_path_is_only_inspected(self):
        fake=self.base/'fake harness';(fake/'resources').mkdir(parents=True)
        (fake/'DeepSeek Harness.exe').write_bytes(b'not executable')
        (fake/'resources/app.asar').write_bytes(b'not archive')
        with patch.dict(os.environ,{'DEEPSEEK_HARNESS_HOME':str(fake)}),patch.object(subprocess,'Popen',side_effect=AssertionError('must not launch')):
            command=dispatch.launch_command()
        self.assertEqual(command[0],str(fake/'DeepSeek Harness.exe'))
        self.assertIn('headless',command)

    def test_packaging_exact_allowlist_excludes_unlisted_data(self):
        copied=self.base/'copy';shutil.copytree(ROOT,copied,ignore=shutil.ignore_patterns('__pycache__'))
        (copied/'secret.json').write_text('{"token":"PRIVATE_TEST_SENTINEL"}')
        (copied/'notes.md').write_text('PRIVATE_TEST_SENTINEL')
        spec=importlib.util.spec_from_file_location('copied_package',copied/'scripts/package.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        destination=self.base/'review.zip';module.build(destination)
        with zipfile.ZipFile(destination) as archive:
            names=archive.namelist()
            self.assertFalse(any(n.endswith(('secret.json','notes.md')) for n in names))
            self.assertTrue('deepseek-collab-hub/scripts/package.py' in names)
            self.assertTrue('deepseek-collab-hub/LICENSE' in names)
            self.assertIsNone(archive.testzip())
        with self.assertRaises(ValueError):module.build(copied/'bad.zip')

if __name__=='__main__':unittest.main()
