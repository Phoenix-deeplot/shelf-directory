# API Function 收敛与采集边界整理（2026-11-05）

状态：用户已确认实施范围；本计划待提交到 master，分支开发前再次核对。

## 1. 用户任务

- 保留现有 API 外部 URL 和请求/响应行为。
- 降低 Vercel Python Function 数量，避免随业务 endpoint 数量增长。
- 将路由入口与领域逻辑分开，按热榜、订阅、内容、运营组织内部模块。
- 为 RSS/热点采集任务建立可复用边界；本轮不部署外部 worker、不推送、不部署。

## 2. 规划（Codex）

### 分工与范围

| 任务 | 负责 | 分支 / worktree | 预计文件 | 不许碰 |
|---|---|---|---|---|
| API 统一路由、Vercel rewrites、采集调用边界及兼容性测试 | Codex | `codex/api-function-consolidation` · `E:\Ztree\wt\codex-api-function-consolidation`，从包含本计划的最新 master 开始 | `vercel.json`、新增 `api/router.py`、新增/调整 `api` 内路由适配模块、采集任务可复用模块、`tests/test_api_router.py`（新增）、必要时现有 API 路由测试 | 不碰 `my-archive`、其他 Codex/Claude worktree、用户现有 `.gitignore`；不改线上数据库/变量、不运行生产迁移、不推送、不部署 |

`docs/work/_TEMPLATE.md` 在仓库中不存在；本文件沿用已有 `docs/work/2026-11-04-hot-analytics.md` 的结构。

### API 与部署约定

- 保留现有公开路径：`/api/admin`、`/api/book`、`/api/feed`、`/api/feedback`、`/api/hot`、`/api/hot/events`、`/api/hot/events/refresh`、`/api/listen`、`/api/ops`、`/api/stat`、`/api/submit`、`/api/sync`、`/api/translate`。
- 所有既有 method、query/body、状态码、响应头、鉴权和缓存语义不主动改变；路由层只负责按路径和方法转发，领域处理继续调用既有模块。
- 使用 `vercel.json` rewrite 将原路径映射到少数统一 Python function。验收时必须确认最终构建产物 Function 数不超过 Hobby 的 12 个；目标是 1 个 API 入口。若 Vercel 对当前 Python 文件结构或 rewrite 优先级不支持该目标，停止大范围迁移，退回到最小合并方案并报告，不猜测部署行为。
- `api/hot/events/refresh` 继续走 `hotapi.handle_event_refresh` 原鉴权逻辑，不允许普通公开 GET handler 代替或绕开鉴权。
- router 内部按稳定路径表分发，不接受客户端传任意模块名、Python import 路径或内部文件路径。
- 不暴露或记录请求正文、凭证、用户阅读内容；保留各 endpoint 现有日志策略。

### 采集任务边界

- 本轮只把目前由 HTTP 请求调用的采集逻辑与前台读接口分开，整理成可由内部调用的、可测试的采集函数/适配边界。
- 前台 `GET` 继续读取现有持久化数据或缓存；刷新端点继续执行当前的授权校验和刷新语义。
- 不创建 GitHub Actions workflow、不引入新云服务、不新增生产 secret，不改变当前调度频率。外部 worker 的部署留待后续单独确认。
- 采集函数要求可注入 fetcher/时间，重复执行安全；新增来源/队列/数据库 schema 不在本轮范围。

### 验收

- 路由层测试覆盖 13 个旧路径、GET/POST/OPTIONS 允许方法、未知路径、方法不允许、查询参数与请求体透传、特殊 refresh 鉴权路径。
- 运行 `python -X utf8 -m unittest tests.test_hot_events tests.test_hot tests.test_listen tests.test_feedapi tests.test_feedback tests.test_admin tests.test_bookapi tests.test_opsapi tests.test_stat tests.test_submission tests.test_syncapi tests.test_translateapi tests.test_api_router`（不存在的旧测试模块先按实际文件名调整，并说明）。
- 运行 `python -m json.tool vercel.json` 验证配置 JSON；有可用 Vercel CLI 时本地构建/检查 Function 产物。若 CLI builder 仍报环境已知错误，不将其误报为代码通过，保留该限制。
- 检查 `git diff --check`、路由无重复或遗漏、git status 只含规划/本任务文件。
- 不连接线上数据库、不部署、不推送。

### 端口与风险

- Codex 端口段：8900–8999、9450–9459；本任务预计无需启动页面服务。
- 路由 rewrite 的优先级和 Python Function 编译发现规则必须用本地构建结果验证。若大规模统一入口不能在当前 Vercel 配置中可靠工作，优先采用最小收敛（至少 13→12）而不是冒险破坏旧 API。
- master 的 `.gitignore` 存在用户暂存修改，保持原样；master 只提交本规划文件，业务代码只在隔离 worktree 修改。

## 3. 进度

### Codex

- 已检查 `git status`、`git log -3` 与 `git worktree list`；master `.gitignore` 有用户暂存更改，保持不动。
- 当前有其他 Codex worktree `codex/hot-analytics-service`、`codex/polymarket-daily-weekly-api`，均不复用、不改动。
- 当前 `api/` 有 13 个 Python 入口；热点事件读取与刷新分别占用两个入口，刷新鉴权位于 `hotapi.handle_event_refresh`。
- 用户已确认上述实施范围；待规划提交并核对后进入隔离分支开发。

## 4. 合并记录

- 用户检查预览并明确说“合并”前，不合并、不推送、不部署。
