"""Validate and export the skill, dashboard and local tools as one plugin."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import zipfile


def build(output):
    root = Path(__file__).resolve().parents[1]
    output = Path(output).resolve()
    if output == root or root in output.parents:
        raise ValueError('安装包应写在插件目录之外')
    portable = json.loads((root / 'plugin.json').read_text(encoding='utf-8'))
    legacy = json.loads((root / '.codex-plugin/plugin.json').read_text(encoding='utf-8'))
    if portable.get('$schema') != 'https://agent-plugins.org/schemas/1.0.0/plugin.schema.json':
        raise ValueError('缺少标准插件 schema')
    if not re.fullmatch(r'\d+\.\d+\.\d+', portable['version']):
        raise ValueError('版本必须使用 SemVer')
    if any(portable[key] != legacy[key] for key in ('name', 'version')):
        raise ValueError('两份清单的名称或版本不一致')
    interface = portable['extensions']['com.openai']['interface']
    if interface != legacy['interface'] or len(interface['shortDescription']) > 30:
        raise ValueError('界面元数据不一致或副标题太长')
    required = ['plugin.json', '.codex-plugin/plugin.json', 'mcp.json', '.mcp.json', 'assets/icon.svg',
                'scripts/server.py', 'scripts/store.py', 'scripts/hub.py', 'scripts/dashboard.py',
                'scripts/dashboard.html', 'scripts/dispatch.py', 'scripts/headless.patch.yml',
                'skills/deepseek-collab/SKILL.md', 'skills/deepseek-collab/references/runtime.md',
                'skills/deepseek-collab/agents/openai.yaml', '试用说明.md',
                'scripts/package.py', 'tests/test_release.py', 'README.md', 'LICENSE-DECISION.md',
                'AUDIT.md', '.gitignore', 'SHA256SUMS.txt', 'LICENSE']
    for name in required:
        if not (root / name).is_file():
            raise ValueError('缺少组件：' + name)
    skill = (root / 'skills/deepseek-collab/SKILL.md').read_text(encoding='utf-8')
    if not re.match(r'---\nname: deepseek-collab\ndescription: [^\n]+\n---\n', skill):
        raise ValueError('技能入口需要 name 和 description')
    for target in re.findall(r'\]\((references/[^)]+)\)', skill):
        if not (root / 'skills/deepseek-collab' / target).is_file():
            raise ValueError('缺少技能参考：' + target)
    config = json.loads((root / 'mcp.json').read_text(encoding='utf-8'))
    old_config = json.loads((root / '.mcp.json').read_text(encoding='utf-8'))
    if config['mcpServers']['collab-hub']['args'] != ['${PLUGIN_ROOT}/scripts/server.py'] or old_config['mcpServers']['collab-hub']['args'] != ['${CLAUDE_PLUGIN_ROOT}/scripts/server.py']:
        raise ValueError('MCP 启动脚本配置不一致')
    # Exact paths, never an extension-based inclusion rule.
    files = []
    for name in required:
        path = root / name
        if any(part.is_symlink() for part in [path, *path.parents] if part == root or root in part.parents):
            raise ValueError('包内不允许符号链接：' + name)
        if path.suffix == '.py':
            ast.parse(path.read_text(encoding='utf-8'), filename=name)
        if path.suffix == '.json':
            json.loads(path.read_text(encoding='utf-8'))
        files.append(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.write(path, Path(portable['name']) / path.relative_to(root))
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None or len(archive.namelist()) != len(files):
            raise ValueError('安装包校验失败')
        for name in required:
            assert portable['name'] + '/' + name in archive.namelist()
    return {'name': portable['name'], 'version': portable['version'], 'files': len(files),
            'archive': str(output), 'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.output), ensure_ascii=False))
