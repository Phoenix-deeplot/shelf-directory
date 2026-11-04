# 热点事件恢复与热榜来源补充（2026-11-04）

状态：已合并（2026-11-04，用户说“除了小程序不合并，其他都合并”）；未推送、未部署。小程序榜依用户指示搁置。

## 1. 任务清单（用户）

- [ ] 恢复不可用的热点追踪/今日精选数据。
- [ ] 热榜补充 X、Polymarket、小程序榜及更多海外热门来源。
- [ ] 小红书、B 站、抖音已有来源应保留并确认是否可用，避免重复加源。
- [ ] 增加 iOS App Store 榜与 Android Google Play 榜，并清楚区分榜单名次与实际下载量。
- [ ] 热点事件服务故障时，不能让主站今日推荐混合流一并消失。

## 2. 规划（Codex）

### 分工

| 任务 | 负责 | 分支 / worktree | 会改的文件 | 不许碰 |
|---|---|---|---|---|
| 热点事件 API 故障诊断和降级行为 | Codex | `codex/hot-seo-sources` · `E:\Ztree\wt\codex-hot-seo-sources` | `hotapi.py`、`tests/test_hot_events.py`，必要时 `tests/test_hot.py` | `my-archive` 仓库、数据库迁移历史、线上数据库/环境变量、用户已有 `.gitignore` 修改 |
| 新增/校验热榜来源 | Codex | 同上 | `hotapi.py`、`tests/test_hot.py`、README（仅接口/环境变量变化时） | 不抓取登录态内容、不提交密钥、不改已存在源的无关解析器 |

仅在用户确认规划后创建 worktree。`E:\Ztree\shelf-directory` 当前 `.gitignore` 有用户修改，保持原样；master 工作区仅提交本规划文件。

### 数据源范围

1. **事件 API**：核对 `handle_events` 对 Redis/Postgres/迁移缺失的处理、缓存与失败响应；保留已有 2.5 秒上游超时。热点追踪不可用时返回明确的机器可读错误原因，避免异常变成 200 空数据。
2. **已有中文平台源**：检查 `xiaohongshu`、`bilibili`、`douyin` 的 parser、TTL、错误诊断和 API catalog；确认 API 公开返回平台列表且单源失败不影响其他榜。它们已存在，不另建重复 id。
3. **X**：可采用配置了服务端 `X_BEARER_TOKEN` 后启用的官方只读来源；需进行少量、限频的趋势/搜索抓取，避免全量时间线。未配置 key 时不展示“空榜”。运营成本/配额需用户接受，密钥只在 Vercel 服务端配置（本任务不改线上变量）。
4. **Polymarket**：新增只读事件榜，优先按 24 小时交易量/流动性排序，并标注更新时间与原站链接；标题/摘要明确这是预测市场数据，不把市场概率写成事实判断、新闻结论或投资建议。需验证官方 Gamma API 的返回结构、限频和地区可访问性。
5. **微信小程序榜**：依用户指示暂缓，不进入本轮实现。
6. **iOS App Store 榜**：优先使用 Apple RSS Builder/公开榜单 feed 读取地区、类别和 Top Free/Top Paid 名次。榜单名次只代表商店排名，不声称为下载量。
7. **Android Google Play 榜**：Google Play Developer API 面向开发者自己的应用管理，并非公共全商店排行榜接口。经核对现有技术栈后，本轮暂不接入依赖抓取/非公开 RPC 的不稳定来源；待采用可维护的正式数据服务或用户指定 provider 后再做。
8. **海外热门**：先恢复已存在的 YouTube、Google Trends、Hacker News、Product Hunt、BBC/Guardian 等可用源；补充海外平台只选择有稳定公开 RSS/API、条款允许摘要与链接展示的来源。X 单独按上项处理。

### 接口约定

- 继续使用 `GET /api/hot` 及 `GET /api/hot/events?lang=zh&limit=...`，尽量不破坏当前 JSON 字段。
- 榜单源沿用 `PLATFORMS` 中稳定 `id`、`col`、`name`、`url`、`feed`、`ttl`、`parse` 等结构。新增平台必须有 parser 单测、来源 URL、缓存/地区策略、错误诊断。
- Events 返回沿用 `{ok, updated, events}` 以及现有事件条目结构；数据库未配置、迁移缺失、上游失败分别返回可识别错误，使用 stale-if-error（若已有缓存）而不是伪造空列表。
- 新源的第三方密钥仅从服务端环境变量读取；不接收用户任意 URL、不代理用户任意请求。

### 验收

- `python -X utf8 -m unittest tests.test_hot_events tests.test_hot` 通过；新增每个 parser 的正常、空数据、坏数据、单源故障测试。
- `GET /api/hot` 至少正确列出新增启用来源及分类；源未配置必要凭证时默认不列出，单个平台上游失败不影响其他平台。
- 事件 API 在 DB 缺失/迁移未执行时诊断清楚；无可用新事件时有缓存则返回缓存，无缓存则明确失败，不会被前端当作成功的空精选。
- Polymarket 卡片包含来源、更新时间和市场性质标识；X 不配置 key 不显示；小程序榜只有确认 provider/credentials/licensing 后才进入实际开发与测试。
- Apple 榜单测试覆盖国家/地区、榜单类型和类别；Google Play 榜实测目标地区的获取稳定性，源失效时下线/标注而非展示伪造排名。除非来源明确提供且授权展示，否则不显示“下载量”数字。
- 不联网依赖线上环境，不运行迁移、不改生产配置；Codex 使用 8900–8999、9450–9459 端口段。

### 风险和要避开的冲突点

- 热点追踪依赖 PostgreSQL/Redis 的实际生产配置以及 `migrations/007_hotlist_events.sql` 是否执行；本地代码存在迁移并不能证明线上已执行。
- X API 是按量计费服务；接入后需控制调用量，避免按每个访客请求外部 API。
- 微信小程序榜属于商业数据源范畴；公开页面出现榜单不代表可批量抓取、商用或在本站重新展示。
- Apple 有公开 RSS 榜单入口；Google Play 官方 Developer API 主要服务于开发者自有应用，开源抓取库通常依赖页面结构或非公开接口，可能变动。
- Polymarket 的赔率/交易量是预测市场信号，不应称为客观热度或事实。
- 保持 `/api/hot` 兼容，避免影响主站目录/订阅卡片和现有热榜页面。

## 3. 进度

### Codex

- 已查阅热榜源代码和测试：小红书、B 站、抖音均已存在；X、Polymarket、小程序榜未发现对应来源实现。
- 事件接口使用数据库支持；仓库有 migration 007。生产是否缺环境变量/迁移尚未证实，需用户提供线上接口响应（去除敏感值）或由部署环境检查确认。
- 等用户确认规划与小程序榜数据供应商后再创建 worktree、改代码。

### 实施记录（隔离分支）

- 已改 `hotapi.py`：新增中国 / 美国 App Store 免费榜、Polymarket 24h 市场活跃度、密钥门控的 X 热点词近期讨论源；保留原有小红书 / B 站 / 抖音定义。
- 事件刷新首次失败且无缓存时现在返回可识别的 502 `upstream`，不再冒充 200 空成功；已有缓存仍 stale-if-error。
- 已更新 `tests/test_hot.py`、`tests/test_hot_events.py` 和 README；本地单测待完成复跑。

## 4. 合并记录

- 合并顺序（2026-11-04，`--no-ff`，没推送、没部署）：`codex/hot-seo-sources` 直接合进 master（master 在它的分叉点之后没有改动别的文件，没有冲突；合并后的文件树和分支完全一致）。
- 合并后的测试结果：`tests` 里全部 24 个模块、724 个单测，723 个通过；失败的 1 个是 `tests.test_hotdetail … test_diag_says_why_a_platform_cannot_be_reached_from_here`（知乎的 host 已经是 `plink.anyfeeder.com`，测试还写着 `news-at.zhihu.com`），在合并前的 master 上就一样失败，不是这次带来的。热榜两个模块 `tests.test_hot`、`tests.test_hot_events` 都通过。
- 删除的分支和 worktree：`codex/hot-seo-sources` 和 `E:\Ztree\wt\codex-hot-seo-sources`。`codex/hotlist-topics-api` 早就合并过，它的 worktree `E:\Ztree\wt\codex-hotlist-topics-api` 还在，这次没动。
- 上线要用户做：shelf-directory 的部署才会让热榜有 App Store / Polymarket、事件接口失败时返回 502（不再是 200 空数据）；X 来源要在服务端环境变量里设 `X_BEARER_TOKEN`（未设置时不列出，密钥不进仓库）；事件接口还需要线上数据库已经执行 `migrations/007_hotlist_events.sql`（这个我没法从这里确认）。
