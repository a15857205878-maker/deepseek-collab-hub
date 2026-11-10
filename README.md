# DeepSeek 协作中心

独立的 Python 本机插件，用于登记项目、搜索协作记录与查看协作板；主动派发任务时对接用户自行安装、配置的 DeepSeek Harness。当前导出版本 0.3.1，基于现用 0.3.0 独立修订；没有修改已安装版本。

用户已明确授权本项目自有代码采用 MIT，见 LICENSE；第三方内容继续适用原条款。导出图标已替换为本轮直接编写的中性几何 SVG，占位图标适用 MIT；不包含来源未确认的旧图。许可与署名说明见 LICENSE-DECISION.md。

## 安装与使用

1. Windows，Python 3.10+（本次实测版本见 AUDIT.md）；归档/MCP/面板仅用标准库，无 pip 依赖。派发及停止进程的逻辑面向 Windows。
2. 将整个插件目录放在任意可读路径。支持 Agent Plugins 1.0 的宿主使用 plugin.json + mcp.json；兼容配置位于 .codex-plugin/plugin.json + .mcp.json。按宿主的本地插件安装说明选择目录。没有执行安装或账号连接。
3. mcp.json 使用 `${PLUGIN_ROOT}/scripts/server.py`，兼容 .mcp.json 使用 `${CLAUDE_PLUGIN_ROOT}/scripts/server.py`。若宿主不展开变量，手动在宿主设置中填本机解压路径。不同宿主的占位符兼容性需实际验证。
4. 命令行可直接运行（把 `<plugin-root>` 替换为本包路径）：

```powershell
$env:DEEPSEEK_HUB_HOME = '<your-private-data-directory>'
python '<plugin-root>/scripts/store.py' --query ''
python '<plugin-root>/scripts/hub.py' --start
```

第二条命令会启动本机协作板，返回含访问 token 的私有 URL。不要公开 URL、数据目录、日志或数据库。登记目录会读取其中已有的协作记录，务必只登记用户指定的工作区。

## 可选 Harness 依赖

不附带 DeepSeek Harness、模型、凭据或账户。仅检索记录无需 Harness。派发功能依赖 Harness 的桌面 host 内部 CLI 路径，未来 Harness 更新可能破坏兼容；未验证任一公开稳定 API 或当前版本兼容性。

默认寻找 `%LOCALAPPDATA%/Programs/DeepSeek Harness`。非默认安装可设置 `DEEPSEEK_HARNESS_HOME` 为包含 DeepSeek Harness.exe 和 resources/app.asar 的目录。先做无启动预检：

```powershell
$env:DEEPSEEK_HARNESS_HOME = '<your-harness-install-directory>'
python '<plugin-root>/scripts/dispatch.py' --preflight
```

预检仅检查路径，成功也不证明认证、CLI 或模型可用。认证通过 Harness 自己配置，插件不读取或分发凭据配置。headless.patch.yml 的 credentials enabled 补丁沿用本机 Harness 凭据服务，不包含凭据值。明确授权实际协作后，才按 skills/deepseek-collab/references/runtime.md 派发。

## 验证与打包

```powershell
python -m unittest discover -s '<plugin-root>/tests' -v
python '<plugin-root>/scripts/package.py' --output '<outside-plugin-root>/deepseek-collab-hub-review.zip'
```

package.py 使用精确文件白名单，不会把新增任务历史、日志、数据库或凭据混入包。压缩包根目录为 deepseek-collab-hub；ZIP 中的版本为 0.3.1。SHA256SUMS.txt 校验插件源文件（不含清单自身），ZIP SHA256 在包旁的验收报告中。测试完全使用临时合成资料，不连接模型、不启动 Harness。
