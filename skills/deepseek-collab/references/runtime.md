# 包内脚本与运行位置

先根据当前读到的 `SKILL.md` 定位插件根目录：它位于 `skills/deepseek-collab/` 的上两级。使用下列脚本的绝对路径，不依赖聊天的当前目录，不硬编码某个缓存版本。

运行依赖：本机 Python 3.10+ 与已安装、已配置的 DeepSeek Harness。协作板、归档与 MCP 适配只依赖 Python 标准库；查看记录不需要启动 Harness。

## 工具尚未加载时

```powershell
python '<plugin-root>/scripts/store.py' --register '<absolute-workspace>' --name '<project-name>'
python '<plugin-root>/scripts/hub.py' --start
python '<plugin-root>/scripts/store.py' --query '<keyword>'
```

`hub.py --start` 返回当前统一板的真实私有 URL；每次使用都读取本次返回值，不拿其他项目的旧链接代替。后台服务以隐藏窗口启动，存活时间取决于进程；不设置开机启动或持久监控。

MCP 检索支持 `query`、`project_id`、`status`、`limit`（1–1000）和 `offset`。板上展示最近 200 轮，每轮最近 300 个公开事件；完整事件用 `get_task`，更早记录用分页检索。

## 初次派发

用文件工具或 PowerShell 字面 here-string 写 UTF-8 任务文件，不把用户文本插值进 shell 命令。

```powershell
python '<plugin-root>/scripts/dispatch.py' --workspace '<original-workspace>' --task-file '<absolute-task-file>' --timeout 1200
```

脚本登记原工作区，并在退出时同步归档。派发和恢复都在原工作区执行，不能在归档目录中修改项目。

## 续接

```powershell
python '<plugin-root>/scripts/dispatch.py' --workspace '<same-original-workspace>' --task-file '<absolute-followup-file>' --session-id '<original-state.session_id>' --timeout 1200
```

只有本工具创建的 headless 会话可以续接，不接管活跃的 Harness 桌面聊天。面板中的调整先停止旧任务，再在同一工作区续接。

## 故障检查

记录保存在原工作区的 `work/deepseek-collab/runs/<run-id>/`：检查 `state.json`、`result.json`、`answer.md`（如果存在）、`events.jsonl` 和 `stderr.log`。不要把未经筛选的日志发到聊天里。没有结果文件时，先检查已保存事件和实际文件，确认是否仍活跃。

认证补丁只修复 headless preset 的默认凭据禁用项，沿用本机凭据服务，不改变桌面配置或削弱审批设置。不要打包任何用户凭据文件。

## 自定义安装与无启动预检

可设置 `DEEPSEEK_HARNESS_HOME` 指向 Harness 安装目录，再执行 `python <plugin-root>/scripts/dispatch.py --preflight`。缺依赖会非零退出，预检不会派发模型或读取凭据。桌面内部 CLI 不是已验证的稳定外部接口，升级 Harness 后需另行验证。
