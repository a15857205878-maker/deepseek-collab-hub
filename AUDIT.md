# 公开源码导出审计

## 来源与修订

基于现用缓存 0.3.0，与候选 owning source 的 17 个白名单文件逐一 SHA256 比较：16 个相同，只有 scripts/package.py 不同。采用现用版本为导出基线，两处原目录均未修改。现用 package.py 包含旧清单校验及通用扩展名遍历；这次导出保留元数据/语法校验，改为精确文件白名单，并将 package.py 自身、测试、说明及 LICENSE 纳入包。导出版本提升为 0.3.1，两份清单同步。assets/icon.svg 仅在导出副本替换为本轮直接编写的中性几何协作占位图标（相连矩形），适用 MIT；来源未确认的旧图不导出，现用图标未改。

MCP 配置使用插件根变量，无机器绝对路径。dispatch 原先依赖 LOCALAPPDATA 默认目录，导出增加 DEEPSEEK_HARNESS_HOME 和 --preflight；缺依赖在写入索引前退出。Harness 内部 host CLI 和 Windows taskkill 依赖仍存在，未声称跨平台派发或已验证最新 Harness 兼容。

## 排除项

未复制任务历史、数据库、DeepSeek-Collab-Data、request/state/result/events、answer、日志、dashboard-config、实际访问 token、凭据、提供商设置、私人推理、Harness 安装、Python 缓存、运行时、node_modules、Git 目录、旧 ZIP 或本机源绝对路径。测试内 PRIVATE_TEST_SENTINEL 是合成过滤检测值。

## 验证范围

使用 Python 3.12.3 的标准库。离线测试覆盖中文空格换位置 MCP stdio initialize/tools/list/空数据查询、合成记录归档/搜索/私人思考过滤/删除恢复、缺 Harness 非零无副作用、路径覆盖不启动进程、精确打包白名单排除陌生 JSON/Markdown、包内 LICENSE 及 ZIP 完整性。逐文件 SHA256 见 SHA256SUMS.txt；清单自身不递归散列。

没有启动 Harness、模型、真实任务或续接；没有读取用户任务数据或凭据，没有装入宿主，未启动面板浏览器。未执行从零 Python 安装、真实 Harness CLI/认证、宿主安装及占位符展开、UI 交互验证。归档/MCP 可运行不等于模型派发或界面已经端到端验证。

MIT 采用用户本轮明确授权。发布账号已确认，原有中性署名和第三方条款保持；旧图来源阻碍已通过自有几何 SVG 替换消除。本文件记录源码导出与验收边界。
