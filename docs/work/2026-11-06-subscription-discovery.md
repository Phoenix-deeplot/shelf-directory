# 订阅解析升级（2026-11-06）

状态：用户已确认范围，开发中。

前端完整规划：my-archive/docs/work/2026-11-06-subscription-discovery.md（master af67119）。

本仓库 Codex 分支：codex/subscription-discovery，worktree：E:/Ztree/wt/codex-subscription-discovery-api。
可修改：feedresolver.py、feedapi.py、tests/test_feedresolver.py、必要的 tests/test_feedapi.py、.env.example、本记录。
不改 aggregate.py 公共采集规则、音乐播放、推荐、数据库、路由数量。主仓库已暂存 .gitignore 保留。

接口：POST /api/feed action=discover 保留 feed/link；新增可选 resolution 元信息。RSSHub 路由须由 RSSHUB_BASE 提供且经过 Feed 验证。发现候选均走 aggregate 的安全网络入口。update 仍读取验证后的真实 Feed 地址。

验收：tests.test_feedresolver、tests.test_feedapi、tests.test_aggregate、tests.test_api_router；真实 pod.link 与其他公开 Feed 抽测；前端预览用 8900–8999 / 9450–9459。
不推送、不部署、不合并。
