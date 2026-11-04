# 热榜来源分层与匿名实验分析 API（2026-11-04）

状态：已确认，准备开发；不合并、不推送、不部署。

## 1. 用户任务

- [x] 按浏览器语言/地区排序热榜；增加「应用与游戏」源类别；AI 和学习内容分层。
- [x] 支持「今日焦点」最多三条事件卡，兼容现有平台榜单接口。
- [x] 增加最小化匿名曝光/点击/活跃时间事件收集、A/B 配置、运营汇总数据接口。
- [x] 运营分析在现有密码验证的 Admin/Operations 界面展示。
- [ ] 财经、工业、吾爱破解等源只做来源健康/质量验证，不在未通过验证时启用。

## 2. 规划（Codex）

### 分工

| 任务 | 负责 | 分支 / worktree | 会改的文件 | 不许碰 |
|---|---|---|---|---|
| 后端榜单元数据、埋点 API、实验管理/汇总、DB migration、运营数据面板与单测 | Codex worker | `codex/hot-analytics-service` · `E:\\Ztree\\wt\\codex-hot-analytics-service`；继续自 `codex/polymarket-daily-weekly-api`，另建 worktree，旧预览目录保持不动 | `hotapi.py`、`analyticsapi.py`（新增）、`api/analytics.py`（新增）、`opsapi.py`、`migrations/008_hot_analytics.sql`、`docs/admin/index.html`、`docs/admin/admin.js`、`docs/admin/admin.css`、`tests/test_analytics.py`（新增）、必要时 `tests/test_opsapi.py` | 不碰 my-archive；不改用户 `.gitignore`；不运行生产迁移；不改 Vercel 线上变量/数据库；不存原始 IP/PII；不编辑静态前端接口约定 |

### API 契约（需与 my-archive 同名计划保持一致）

#### 榜单目录

- `GET /api/hot?format=json&lang=zh` 响应兼容已有 `{ok,columns,platforms}`；可增添 `locale_hint`（`language`、可信的 `country_code`），不删除/改名现有字段。
- 榜单类别 ID 新增 `apps_games`；App Store/Steam/TapTap 等来源按类别标注。对平台语言使用已有 `lang` 属性；客户端按用户手动选择 > 浏览器语言 > 服务端默认排序。IP 国家码只能作为地区榜提示，不持久化 IP。
- AI 来源元数据可增加 `track`（`vendor`、`research`、`tools`、`applications`）和 `source_tier`（`primary`、`secondary`）；学习内容保留源榜排名，订阅内容仅提供发布时间顺序，禁止把二者伪装为同一原始榜。
- 当前明确有 RSS/API 的源可分类；财经、工业与论坛候选需健康试验，接口目录默认不启用未经验证来源。

#### 实验读取与写入

- `GET /api/analytics/config?experiment=hot-layout-v1`：公开只读响应 `{ok,experiment:{id,status,allocation,variants:[{id,label}]}}`；停止/不存在时返回 `status=off`。不包含任何用户分组数据。
- `POST /api/analytics/events`：仅接受事件批次 `{events:[...]}`，限制体积、批次数和字段长度；枚举事件为 `experiment_exposure`、`item_impression`、`item_click`、`active_time`。响应 `{ok,accepted,rejected}`；不得让浏览器传入任意 SQL、URL 或无约束 JSON。
- `POST` 必须校验 Origin allowlist、JSON/body 大小、事件白名单、幂等 event_id，按匿名 ID/来源限频；匿名 ID 应由前端随机生成并在服务端以 SHA-256/HMAC 摘要存储。原始 IP 不写表、不入日志。地区只存可信边缘注入的 ISO country code。
- A/B 分组在浏览器用匿名 UUID 稳定分配，数据库保存配置（变体、比例、开始/结束/状态），不按 IP 或真实身份分配。首轮两组比例 50/50。

#### 管理汇总

- 通过现有 `/api/ops` 的密码 session 增加 `analytics-config`、`analytics-summary` action；不公开 GET 分析数据，不另建弱认证管理入口。
- 汇总输入仅允许实验 ID、`from` / `to` 日期、语言、设备和栏目筛选；强制最大 90 天窗口。
- 输出每个变体有效曝光、独立点击、CTR、前台活跃秒数均值/中位数、收藏/订阅（若已存在可关联事件）、按栏目/来源的曝光点击明细和样本是否不足状态。定义和去重口径由接口固定，UI同步显示。

### 数据库 migration

- 新建 `ops.analytics_experiments`：实验 ID、状态、变体 JSON/比例、开始/结束时间、更新时间。
- 新建 `ops.analytics_events`：幂等事件 ID、时间、实验/变体、anon ID 摘要、session ID、事件名、module/content/program/platform key、locale、country code、device class、active seconds；约束允许的事件/秒数，索引覆盖日期/实验/变体和栏目汇总。
- 事件保留 90 天，增加仅清理过期数据的安全管理命令或由后续定时任务执行；首轮不做逐请求全表重算，查询使用预聚合 SQL，避免并发全表成本。
- 管理 API 应检查 schema；未应用 008 migration 时返回可读 `schema` 错误及缺表/字段，不暴露 SQL 或数据库凭证。公开事件采集在数据库未配置时返回可观察的 `503 database`，前端需吞掉此错误且不能阻断阅读。

### 运营页

- 在现有 Admin/运营页新增「实验分析」tab，复用现有密码登录和视觉组件，不改其他栏目/订阅功能。
- 可创建/编辑/暂停实验：名称、实验 key、变体标签、流量比例；同一 experiment key 同时最多一个 active。
- 数据区：日期范围、实验、语言、设备、栏目筛选；对比控组/变体的曝光、CTR、有效停留、收藏/订阅转化；按来源/内容展示点击 top 表。
- 样本低于阈值时显示“样本不足，暂不判断胜负”，不自动选择 winner 或变更线上配置。保存成功/失败显示原因。

### 安全与隐私

- 仅收匿名随机 UUID 的摘要与内容交互数；不存原始 IP、完整 UA、输入搜索词、访问内容正文或账号信息。
- allowlist event names / field keys / value lengths；设置 CORS 和速率上限；不能因为 dashboard page 本身可公开访问而泄露活动数据。
- 本地/生产共用迁移脚本，但本任务只新增迁移文件和 mock 单测，不连接、不迁移线上数据库。

### 验收

- `python -X utf8 -m unittest tests.test_analytics tests.test_opsapi tests.test_hot` 通过（如修改热榜测试则跑全热榜相关测试）。
- API 单测覆盖：配置缺失/停用、有效及非法事件、Origin/载荷/频控、幂等、数据库缺失/迁移缺项、权限、90 天范围、汇总口径。
- 运营页 E2E 或 DOM 级测试覆盖：登录后可见实验分析 tab，筛选与变体汇总正确，未登录不能获取分析结果，接口错误有原因。
- 和前端联调契约：事件曝光 50% + 1 秒；停留前台累计，每 15 秒增量；`sendBeacon` 切页补发；同 session / content 去重。事件路径/字段变动须同步 my-archive 计划。
- 不推送、不部署、不接入线上真实密钥或执行迁移。Codex 端口段 8900–8999、9450–9459。

### 风险

- 低流量不适合过早判定统计显著性；admin 显示样本数和方向性，不自动宣布胜者。
- 数据接口必须有防滥用约束；数据库收费与性能应根据实际事件量监控。
- A/B 服务可上线后再开实验；计划/本地测试不能证明线上 Postgres 已完成迁移。
- 此 worktree 的 API 分支以当前用户预览的 Polymarket 分支为祖先；保留并行 preview，不改原 worktree。

## 3. 进度

### Codex

- 已查看 `git status`/`git log -3`；`.gitignore` 是用户现有改动，保持不动。
- 用户要求进入实施阶段；master 只提交本计划文件，服务代码进入隔离 worktree。

### Codex worker

- API/运营管理实现待开始。

## 4. 合并记录

- 用户检查两个本地预览并明确说“合并”前不合并、不推送、不部署。
