# 订阅解析升级（2026-11-06）

状态：用户已确认范围，开发完成，待预览合并。

前端完整规划：my-archive/docs/work/2026-11-06-subscription-discovery.md（master af67119）。

本仓库 Codex 分支：codex/subscription-discovery，worktree：E:/Ztree/wt/codex-subscription-discovery-api。
可修改：feedresolver.py、feedapi.py、tests/test_feedresolver.py、必要的 tests/test_feedapi.py、.env.example、本记录。
不改 aggregate.py 公共采集规则、音乐播放、推荐、数据库、路由数量。主仓库已暂存 .gitignore 保留。

接口：POST /api/feed action=discover 保留 feed/link；新增可选 resolution 元信息。RSSHub 路由须由 RSSHUB_BASE 提供且经过 Feed 验证。发现候选均走 aggregate 的安全网络入口。update 仍读取验证后的真实 Feed 地址。

验收：tests.test_feedresolver、tests.test_feedapi、tests.test_aggregate、tests.test_api_router；真实 pod.link 与其他公开 Feed 抽测；前端预览用 8900–8999 / 9450–9459。
不推送、不部署、不合并。

实现：新增独立 feedresolver，支持协议转换、pod.link/Apple lookup、GitHub releases、配置化 B站 RSSHub 和 rsshub://；原生 Feed 仍走 aggregate 的大小、SSRF、时间与解析限制，增加文章显式 Feed 和 YouTube channel ID 发现。非标准 JSON API 与平台权限有独立错误码，HTTP 失败保留兼容错误并增加 reason。

验收：156 项 Feed/aggregate/router 回归通过；之后新增私网候选拒绝与 JSON API 用例，52 项 resolver/Feed 回归通过。前端移动端 8 项真实端到端通过。在线 pod.link 约 1.2 秒成功解析《鲶鱼夜话》近期 50 条，确认订阅后首次刷新成功；阮一峰 Atom、JSON Feed 官方源、RSSHub GitHub releases 均在线成功。YouTube 此网络 404/超时，B站没有配置 RSSHub，均不标记在线通过。

部署只需现有 Python API 打包新模块；不增加 Function、不新增数据库表。需要 B站订阅时，站长配置 RSSHUB_BASE 为自己的可用 HTTPS 实例，凭据仍在实例端。具体抽测明细在前端同名规划。
