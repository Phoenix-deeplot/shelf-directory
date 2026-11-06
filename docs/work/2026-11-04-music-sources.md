# “听”的来源适配器、搜索、找整首、歌词、快照（2026-11-04）

状态：已确认（2026-11-06 用户说“你先完成跟音乐相关的”，B1 由 Claude 做，Codex 在做视频观看体验）；开发中；未推送、未部署

2026-11-06 补充的做法（Claude）：
- 现在 `/api/*` 是 Codex 收敛后的单入口（`api/router.py` → `api_routes/listen.py` → `listen.respond`），所以新接口（`resolve`、`search`、`pool`）都只是 `listen.handle` 的新查询参数，不新增文件入口。
- 新代码放在新模块 `music_sources.py`（波点、抖音热歌榜、搜索、合并、解析、熔断、`pool`、健康记录），`listen.py` 只加几行把它接进 `handle()`、`groups()`、`diag()`；旧字段一个都不改。
- 抖音热歌榜的歌**没有地址**（地址要现取）：只有前端带 `caps=resolve` 时，榜单里才出现它、每首带 `resolve: true`；不带的旧页面看不到这个榜，也就不会点到放不了的歌。这一条是对接口约定的补充，同步写进前端规划。
- 搜索用到的各家接口：Deezer `search`、iTunes Search、Audius `tracks/search`、Jamendo（有 key 才问）、波点；解析器各自有正常 / 空 / 坏数据测试。

前端规划和**接口约定**以 `my-archive/docs/work/2026-11-04-music-player.md` 为准（那里的“接口约定”一节）；改动先改那里并通知前端。

## 1. 任务清单（用户）

- [ ] 接入多个音乐 API，遇到试听可以换音源，各家自带的榜单 / 歌单进来。
- [ ] 搜索歌曲，找整首。
- [ ] 歌曲加载提速和缓存。
- [ ] 国内推荐国内的榜单和音乐人，国外推荐国外的。
- [ ] 歌词：来源有返回就给。
- [ ] 波点音乐默认打开；网易云先用现在的直连；Jamendo 保留；波点所在站的抖音热歌榜当榜单。

## 2. 规划（Claude 起草，Codex 负责）

### 现状（查过、量过）

- `listen.py`：来源是函数 `apple_pool / deezer_chart / audius_trending / jamendo_pool / netease_pool / youtube_pool / podchart_pool`，`fetch_source` 按元组分发，`fetch_group` 并行最多等 6 秒，`combine` 去重并限制每个艺人的首数；每首歌只有一个 `audio`；结果在暖实例的内存里留 6 小时（`POOL_SECONDS`）、旧的最多再用 36 小时。
- 网易云：`netease_pool` 每个榜单对前 80 首逐个 `HEAD` 检查，慢。
- 波点（小尘 API，实测）：`GET https://api.xcvts.cn/api/music/bdyy?msg=<词>&type=json` 返回 `{code, text, data:[{name, artist, _singer, detail_page, pic}]}`（10 条）；加 `&n=<序号>` 返回 `{code, data:{name, artist, cover, detail_page, play_url, lrc}}`；`type=lyric` 只返回歌词文本。`play_url` 是 `http://car-er.kuwo.cn/<令牌>/<时间戳>/….flac`，会过期；把 `http://` 换成 `https://` 也是 200；`bf` 音质参数无效，永远是整首无损 FLAC（27–55 MB）；`HEAD` 带或不带 Referer 都是 200（只测了 HEAD，没在浏览器里测）。耗时：搜索 0.4–0.9 秒，详情 0.9–1.1 秒。没有榜单、没有推荐。
- 抖音热歌榜：`GET https://api.xcvts.cn/api/hotlist/dy/music` → `{code, message, name, title, subtitle, from, total, updateTime, data:[{id, title, album, artist, pic, lyric, url, mobileUrl}]}`，65 首，1.3 秒。
- 同一个站的“QQ&网易云聚合”`type=playlist` 实测 25 秒超时，**不用**。
- 这些接口的条款：“个人兴趣开发、仅供学习交流”，运营者不负责；没有 SLA。Jamendo API 免费档只给非商业。

### 做什么

1. **来源适配器**：把现有来源改成统一的适配器：`id`、`caps`（`search / charts / recommend / full / preview / lyrics / embed`）、`net`（`cn / global / any`）、`enabled(env)`、`health()`；`fetch_source` 变成查表。**现有 `/api/listen` 返回的字段一个都不变。**
2. **波点适配器 `bodian`**：`caps = search + full + lyrics`；默认开，`LISTEN_BODIAN=0` 关；只在 `resolve` / `search` / `lyric` 里调用，**不放进榜单生成**（地址有时效）；强制 https（改写后 `HEAD` 通了才给）；超时 8 秒；连续 3 次失败暂停 10 分钟（暖实例内存里记）；候选标 `big: true`、`size`（`HEAD` 的 `Content-Length`，拿不到就不写）；不代理音频；歌词（`lrc`）只在 `resolve` 的响应里带出，不存。
3. **抖音热歌榜适配器 `dychart`**：只当榜单元数据（歌名、歌手、封面、名次），不使用 `lyric` / `url` 字段；放在“中国”地区的榜单里；音频靠候选解析。
4. **网易云**：保持现有直连（`LISTEN_NETEASE_DIRECT=1`）；去掉榜单生成时的逐首 `HEAD`，改成 `resolve` 时检查；官方 OpenAPI 等权限确认后再做（本期不做）。
5. **Jamendo**：保持；按“场景 / 风格”归类，不进榜单。
6. **新接口**：`resolve`、`search`、`lyric`（可并进 `resolve`）、`pool`，字段见前端规划的“接口约定”。`search` 并行问 Deezer、Apple（iTunes Search）、Audius、Jamendo、波点，最多等 3 秒，按“规范化后的歌名 + 艺人 + 时长相差 3 秒内”合并成一首，各家的候选挂在它下面；排除带 `live / remix / 伴奏 / cover` 而查询里没有的。
7. **榜单和分类**：`groups` 加 `region`（`cn / jp-kr / west / global`）；同一地区的各家榜按名次合并去重，每行 `cands` 带各家；新增 `kind: 'scene'`（专注、放松、助眠、运动、通勤、学习），原来的 `chill / focus / sleep` 归到场景；歌单（`kind: 'list'`）带 `cover`。
8. **地区**：`region=auto|cn|global`；`auto` 用 `x-vercel-ip-country`（沿用 `hotapi.py` 的做法：只用于这一次回答，不保存），拿不到再用浏览器语言。
9. **快照和缓存**：榜单、`pool` 在 CDN 上长缓存；上游失败时返回最近一份（不超过 36 小时）并带 `stale: true`；约束：首屏只需 1 个请求，命中缓存 < 300 毫秒。生成方式（定时任务，还是请求时生成加长缓存）由负责的 agent 选，在进度里写清理由。
10. **`diag`**：每个来源的成功率、耗时、最近错误和最近成功时间；响应里不出现任何密钥。

### 不做

- ListenBrainz 相似艺人、Internet Archive、YouTube 嵌入、网易云官方 OpenAPI；Last.fm；社区歌词库；QQ&网易云聚合接口；音频代理。

### 接口约定

见 `my-archive/docs/work/2026-11-04-music-player.md`。

### 分工

| 任务 | 负责 | 分支 / worktree | 会改的文件 | 不许碰 |
|---|---|---|---|---|
| B1 来源、接口、快照 | **claude**（用户 2026-11-06 改给 claude） | `claude/music-sources` · `E:\Ztree\wt\claude-music-sources` | `listen.py`、`api/` 里 `listen` 的入口、`tests/test_listen.py`、新测试、README（只在接口 / 环境变量变化时） | 前端仓库；`hotapi.py`；数据库迁移；线上环境变量；用户的 `.gitignore` 修改 |

### 验收

- `python -X utf8 -m unittest tests.test_listen`（加新测试模块）通过；每个新适配器有 parser 的正常、空数据、坏数据、单源故障测试；用固定样本，不联网。
- 旧的 `/api/listen`（`?g=`、`?diag=1`、无参数）返回的字段和值不变（对旧样本逐字段比较）。
- `resolve`：来源超时 ≤ 8 秒、连续失败熔断、http 地址改写 https、过期地址不进缓存；`search`：部分结果也返回、同一首合并、排除 live / remix。
- `LISTEN_BODIAN=0` 时 `diag` 和接口里都没有波点；不设 `LISTEN_NETEASE_DIRECT` 时没有网易云。
- 全部 `tests.test_*` 跑一遍（已知失败：`tests.test_hotdetail … cannot_be_reached_from_here`，在 master 上就失败，知乎的 host 已经变了）。
- 不联网依赖线上环境，不运行迁移，不改生产配置；（B1 由 Claude 做，用 8800–8899、9440–9449 端口段；单元测试本来不开端口。）

### 风险和要避开的冲突点

- 波点和网易云直连是非官方入口，Jamendo 免费档不含广告站；用户知道并决定默认打开；每项都有开关，后端不代理音频；歌词随来源、不存。
- 小尘 API 是个人维护的免费接口，没有 SLA；所以超时短、熔断、永远有别的候选；它的同站聚合接口实测 25 秒超时，不用。
- 无损大文件（27–55 MB）：候选标 `big`，由前端按网络决定要不要自动选。
- Vercel 函数有 10 秒限制：`search` 和 `resolve` 必须在 3 秒 / 8 秒内返回部分结果。
- 暖实例内存里的熔断和缓存不跨实例，只当“尽量”；前端按失败换源是真正的兜底。

## 3. 进度

### claude

2026-11-06 在分支 `claude/music-sources`（worktree `E:\Ztree\wt\claude-music-sources`）里做完，等用户说“合并”。

- 新模块 `music_sources.py`（波点、抖音热歌榜、搜索、合并、解析、熔断、健康记录、`pool`、新目录）；`listen.py` 只加了接线：`handle()` 的 `caps` / `region` / `search` / `resolve` / `pool`，`groups(v2=)`，`fetch_group(v2=)`，`diag.providers`，`pooled()` 数 `STATS['stale']`，Deezer / Audius 歌单的 `COVERS`，`KIND_ORDER` 加 `scene`，`JAMENDO_KINDS` 加 `workout / commute / study`。
- **旧页面不受影响**：不带 `caps=resolve` 时目录和榜单一字不变（`test_a_page_without_caps_sees_exactly_what_it_saw`）：没有 `region / cover / resolve / cands / hot / market / stale`，没有 `dy-hot`，放松 / 专注 / 助眠仍是 `style`。新形态只给带 `caps=resolve` 的页面。
- 用真实网络各问了一遍（2026-11-06，中国大陆）后改了的几处（单元测试里都有）：Deezer / Audius 查不到词时回的是空列表，这是回答不是故障，不能计入熔断；Apple 的搜索接口中国店永远返回 0 条，中文读者改问台湾店（歌手是繁体，歌手比较改成“繁简体算同一个”）；波点同一首歌会回好几行，合成一行一个声音；Audius 的歌名常写成“歌手-歌名”，要去掉歌手前缀再比；“live”不能在“Alive”里匹配；词在歌名 / 歌手里的排前面，歌名恰好是词的在“晴天+告白气球”这类串烧前面，一个词也不沾的（来源碰巧返回的）去掉。
- 真实环境实测：波点搜索 + 详情 + HEAD 约 2 秒，整首 55 MB 的 FLAC（`big: true, size`），歌词 1534 字符的 LRC；抖音热歌榜 65 首约 0.4–1.2 秒；搜索“晴天 周杰伦”约 1.7 秒，把波点和 Apple（台湾店）合成一行。网易云 / Jamendo 没开（没设环境变量）所以没测真实调用；Deezer / Audius 歌单的 `cover` 在真实目录里有了。
- 测试：新增 `tests/test_music_sources.py`（54 个，固定样本、不联网）；全部 `tests.test_*` 共 829 个通过（原来 775 个）。
- 选择：快照用“请求时生成 + CDN 长缓存 + 暖实例里的旧存货（最多 36 小时，响应带 `stale: true`）”，没有做定时任务——Vercel 免费档的 cron 一天一次，对“每天一批”够用，但上游挂了以后旧存货才是真正的兜底，定时任务反而多一个要看护的东西。需要时再加。
- 没做（计划里写了“本期不做”或不属于 B1）：网易云官方 OpenAPI、`lyric` 单独接口（并进 `resolve`）、跨实例的熔断记录。
- 部署由用户做；`LISTEN_BODIAN` 不设就是开。

## 4. 合并记录

- 合并顺序：
- 合并后的测试结果：
- 删除的分支和 worktree：
