# 公共目录（shelf-directory）

「个人数字图书馆」的公共目录：把很多人的博客、文章、GitHub 仓库、视频订阅源聚合成一份目录，也可以托管读者投稿的 Markdown 文章。
**只记标题、摘要、封面图地址和回到原站的链接**（投稿文章除外，那是读者交给本站托管的）。数据放在这个独立的仓库里，由 GitHub Pages 发布，网页的「公共目录」直接读它。

```
访客在网页上贴一个网址，或在书架里点文章的地球图标（不需要 GitHub 账号）      GitHub 上的申请表（要 GitHub 账号）
        │  POST /api/submit  kind:check  → 显示检查结果（读这个网址、对照目录排重）          │
        │  POST /api/submit  kind:url / post → 写成这个仓库里的一个 issue                  │
        ▼                                                                                ▼
   GitHub issue（三种来源同一个队列）──▶ Action 自动检查并回复结果（识别类型、找到几篇、重复、警告）
        │
        ▼
   你在管理页 /admin/ 输入管理密码，点「通过」或「拒绝」（GitHub 令牌只放在服务器的环境变量里）
        │  通过 = 给 issue 加标签 approved
        ▼
   Action 登记（订阅源 / 仓库 / 单条）或发布文章页 ──▶ registry/、posts/ ──▶ 每天 03:17 UTC 重读
        ▼
   docs/（directory.json、订阅源、OPML、文章页 p/）──▶ GitHub Pages ──▶ 网页「公共目录」
```

规范见 [SHELF-SPEC.md](SHELF-SPEC.md)。

## 本地运行（含 `/api`）

不要用 `python -m http.server`、Live Server 或直接打开 `docs/admin/index.html` 测试管理页：它们只能提供静态文件，**不会运行** `api/admin.py`、`api/ops.py` 等 Vercel Python 函数。

1. 复制 [`.env.example`](.env.example) 为 `.env`，填入本机 PostgreSQL、管理密码（至少 12 个字符）和 GitHub 管理令牌。`.env` 已被 Git 忽略。
2. 在 PostgreSQL 的 `postgres` 数据库中执行 [`migrations/004_ops_complete_repair.sql`](migrations/004_ops_complete_repair.sql)。它会创建 `ops` schema 和运营后台需要的表，不会清空 `public` 或已有运营数据。
3. 从仓库根目录以 Vercel 运行：`npx vercel@64.0.2 dev --listen 3000`，再打开 `http://localhost:3000/admin/`。若 3000 已被占用，请换一个端口，并同步修改 `.env` 的 `DIRECTORY_URL` 与 `ALLOWED_ORIGINS`。

Windows 的 Python 官方安装通常只有 `python.exe`，而 Vercel 本地构建器会调用 `python3`；根目录的 `python3.cmd` 兼容入口会把它转发给正确的解释器。项目不在 `vercel.json` 覆盖函数时长：当前 Python Builder 与 `vercel dev` 在本地会因 `maxDuration` 配置冲突，生产环境则使用 Vercel 项目/套餐的默认时长。

## 一次性设置

### A. 数据和自动更新（约 10 分钟）

1. 在 GitHub 新建一个**公开**仓库（例如 `shelf-directory`），把这个文件夹推上去。
2. 仓库 **Settings → Pages**：Source 选 *Deploy from a branch*，分支 `main`，文件夹 `/docs`。
3. 仓库 **Settings → Actions → General → Workflow permissions**：选 *Read and write permissions*。
4. 打开 **Actions → Daily → Run workflow** 手动跑一次（它也会创建需要的标签）。
5. 打开 `https://你的用户名.github.io/仓库名/directory.json`，能看到数据就成功了。
6. 在 `my-archive` 项目的 `site.json` 里加一行：`"directory": "https://你的用户名.github.io/仓库名/"`，页面左侧就会出现「我的书架 | 公共目录」。

### B. 让访客不用 GitHub 账号就能提交（约 10 分钟）

提交函数在 `api/submit.py`，部署成一个 Vercel 项目（和你部署 `my-archive` 的方式一样）：

1. 在这个文件夹运行 `vercel`，新建一个项目（名字随意，比如 `shelf-directory`），再 `vercel --prod`。
2. 给这个项目加环境变量（`vercel env add 名字 production`，或在 Vercel 网页里）：

   | 名字 | 值 |
   |---|---|
   | `SHELF_REPO` | `用户名/仓库名` |
   | `SUBMIT_GITHUB_TOKEN` | 一个 fine-grained 令牌：只选这个仓库，权限给 **Issues: Read and write**，再加 **Contents: Read-only**（让排重直接读仓库里最新的登记表，刚通过的网址马上算“已收录”；不给也能用，只是要等目录页面重新生成） |
   | `ALLOWED_ORIGINS` | 允许提交的网页来源，逗号分隔，默认 `https://books.zhitree.cn` |
   | `SUBMIT_PER_DAY`、`SUBMIT_MAX_OPEN`（可选） | 每个发送者每天最多提交几条（默认 5，最大 500）、待审队列最多几条（默认 100，最大 1000）。站长要把主站“推荐订阅”页里的几十个源一次推进来时，临时调高 |
   | `IP_SALT` | 一长串随机文字（用来给发送者地址做不可逆的短哈希，用于限流） |

3. 改完环境变量要再 `vercel --prod` 一次。把 Vercel 给你的地址加上 `/api/submit`，写进 `registry/config.json` 的 `"submit_api"`，推送。下一次 Action 生成的 `directory.json` 里就有它，网页的「收录」对话框会自动换成"直接提交 + 写文章"两个标签。
4. 用浏览器打开 `https://…/api/submit`，应该看到 `{"ok": true, "service": "shelf-submit", "configured": true, …}`。

   **想要一次 `git push` 就全部更新**：在 Vercel 里把这个项目连接到 GitHub 仓库（项目 Settings → Git → Connect），以后推送就会自动部署函数和管理页，不用再手动 `vercel --prod`。（环境变量改了仍要重新部署一次。）

函数有两个动作，都不需要访客有 GitHub 账号：

- **检查**（`kind: "check"`，什么都不创建）：先对照目录和审核队列排重（已收录、正在审核、已被拒绝都直接告诉访客），再**去读这个网址**，返回它是什么、找到几篇、最近几条、语言、许可、警告，或者读不了的原因。读取用的是和 Action 同一套受限代码（只走 https:443、只连公网地址并固定到解析出的那个 IP、重定向每一跳重查、大小和时间有限、网页要看 robots.txt），每个发送者 10 分钟最多 20 次。文章只做本地检查（长度、链接、屏蔽词、内容哈希），不联网。
- **提交**（`kind: "url" | "post"`）：同样的排重，再写成 issue。不读这个网址，读取留给 Action。

排重先读**仓库里的** `registry/shelves.json`（刚通过的马上算），再读已发布的 `directory.json`；不用数据库：队列就是仓库里待审的 issue，限流靠 issue 里的一个加盐哈希。

### B3. 可选的“同步码”服务（`api/sync.py`，需要一个 Redis）

让读者在几台设备上用同一个书架，不要账号。浏览器自己生成一个长随机同步码，算出三样东西（id、证明身份的密钥、加密钥匙），之后只把**加密后的东西**发到这个函数；加密钥匙从不离开浏览器，服务端读不了标题和内容，同步码只以哈希保存，丢了无法找回。

- **存储**：一个能用 HTTPS 访问的 Redis，推荐 Vercel Marketplace 的 **Upstash Redis**（连到项目后自动写入 `KV_REST_API_URL` + `KV_REST_API_TOKEN`，或 `UPSTASH_REDIS_REST_URL` + `UPSTASH_REDIS_REST_TOKEN`，两种都认），部署后 `GET /api/sync` 显示 `"configured": true`；没配置时页面不显示“同步”。
- **接口**（都是 `POST /api/sync`）：`pull`（取加密的书单，没变化时只回 `unchanged`）、`push`（用修订号做比较并替换，别的设备刚同步过就回 409）、`body_put` / `body_get` / `body_del`（加密并压缩过的小文本，名字由浏览器用 HMAC 取）、`destroy`、`poll`（“愿意付费吗”的匿名计数）。比较并替换用一段 Lua 脚本，是原子的。
- **限制**：每个同步码约 1200 万个字符（`SYNC_QUOTA`），一份书单 ≤ 100 万、一份文本 ≤ 70 万字符、最多 600 份文本；`SYNC_TTL_DAYS`（默认 180）天不用就由 Redis 自己清掉（每次使用都续期）；`SYNC_MAX_KEYS`（默认 6000，约 1000 个同步码）：数据库里的键超过它就不再接收新的同步码；每个访客每天最多新建 3 个，拉取 / 推送 / 文本有每分钟的软限制。来源只认 `ALLOWED_ORIGINS`。
- **付费**：现在没有付费；每个同步空间有一个可以以后接“授权码”的位置，但还没做。页面在用满 20 分钟后问一次“愿意付费吗”，回答用 `poll` 计数，`GET /api/sync` 里的 `poll` 就是结果。
- 测试：`tests/test_syncapi.py`（32 个：权限、比较并替换、并发、额度、过期、限流、HTTP 面、Upstash 的线上格式）；页面一侧的两设备端到端测试在草稿目录里。

### B2. 读者订阅的“更新服务”（`api/feed.py`，不用任何设置）

主站的读者可以在“添加 → 订阅”里订阅自己想读的博客和文章源，订阅清单和文章只存在他们自己的浏览器里。网页读不了别人的网站，所以由这个函数代读。它**不存任何东西**：没有数据库、不要 GitHub 令牌、不要环境变量，和提交服务部署在同一个 Vercel 项目里，部署完就有。

- `POST /api/feed`，三个动作：`{"action":"read","url":…}`（这个地址上的文章：标题、作者、日期和正文，正文是白名单重建的安全 HTML；读不了的返回 422 和原因 `reason`：`login` / `paywall` / `blocked` / `needs_js` / `empty` / `pdf` / `gone` / `unreachable` / `unsupported` / `robots`，网页据此改去原站）；`{"action":"discover","url":…}`（这个网址是订阅源还是一个页面？订阅源返回名字和最近几篇，页面返回标题和摘要，**单个页面能读的话还带上正文**）；`{"action":"update","feeds":[{"url","etag","modified","content"}]}`（一次最多 20 个，返回每个源的新文章，没变化的返回 `unchanged`，读不了的返回原因，一个出错不影响别的）。`"content": true` 时，每个源最新的 12 篇里，订阅源**自己带了全文**的会多一个 `content`（经 `htmlsafe.py` 从白名单重建的 HTML：没有脚本、样式、框架、表单、事件属性，地址都是 http(s)，图片升到 https）和 `content_cut`（超过 6 万字符被截断）；只带摘要的源（文字不够 500 字）没有这个字段；一次回答的正文总量限约 150 万字符，后面的源先丢。
- 读取用的是目录自己的受限代码（`aggregate.py`：只走 https:443、只连公网地址并固定到检查过的那个 IP、重定向每一跳重查、限制大小和时间；普通页面还要 robots.txt 允许），比 Action 里的更紧；返回的是标题、摘要、链接、日期、语言、类型；只有读者要求（`content: true`）且订阅源自己带了全文时才多返回那篇文章的正文，不会再去抓网页，每天的目录构建也不保存任何正文。GitHub 仓库暂时不能订阅。
- **播客**：订阅源里带音频附件（RSS `<enclosure>`、Atom `rel="enclosure"`、JSON Feed `attachments`）的一集，会多一个 `audio`：`{"url","type","dur"}`（声音的地址、类型、秒数；`itunes:duration` 的 `1:02:03` / `62:03` / `3600` 都认），类型 `audio`；整个订阅源还会带 `image`（节目封面，`itunes:image` / `<image>` / Atom `logo`，只收 https）。**服务只交出这些地址，不读、不转存任何声音**，主站的页面自己去播。附件不是音频（视频、PDF、`javascript:` 之类）就没有 `audio`。很长的订阅源（一个播客的全部历史常常超过 2 MB）以前会被拒绝，现在 XML 类的订阅源读到上限为止，只留切口之前完整的几集（新的在前），所以大节目也能订阅；不是订阅源的文件超过上限仍然拒绝。测试：`tests/test_podcast.py`（9 个）。
- **`read` 怎么读**（`readable.py`，只用标准库）：读者点开一篇文章时才读一次，不带 cookie、不登录、用户代理自报 `ZhiTreeBooks-Reader/1 (+https://books.zhitree.cn; …)`，**遵守对方的 robots.txt**（环境变量 `READ_IGNORE_ROBOTS=1` 才忽略，不建议）；找出正文（`article` / `main` / 文字最密的块，去掉导航、侧栏、页脚、广告），重建成安全 HTML，每篇限 15 万字符，超出会说明；要登录、付费墙、人机验证、靠脚本显示、PDF 和别的文件类型识别出来后返回原因。**什么都不保存**。GitHub、视频这类地址不走 `read`。
- 每个来访者每 10 分钟最多 15 次查找、40 次更新、40 次阅读（按暖实例计的软限制，真正的限流在 Vercel）；页面来源和提交服务一样由 `ALLOWED_ORIGINS` 控制（默认 `https://books.zhitree.cn`）。
- 主站页面通过 `directory.json` 里的 `submit_api` 找到它（末尾的 `/submit` 换成 `/feed`）；想指向别处，在 `registry/config.json` 里写 `"feed_api": "https://…/api/feed"`。打开 `https://你的目录站点/api/feed`，应该看到 `{"ok": true, "service": "shelf-feed"}`。
- **订阅源的名字和简介**：`registry/config.json` 里的 `"title"` 是目录自己的名字（`directory.json`、网页标题用它）；Feedly 等阅读器里看到的订阅源名字、OPML 的名字用 `"feed_title"`，名字下面的一句话用 `"feed_subtitle"`，两个都不写就还是 `"title"`。现在是“知树阅读-个人图书空间”加一句主要功能的介绍；分类、标签、书架的订阅源叫“名字 · 分类”。下一次 Action 构建后生效（阅读器里已订阅的会自己更新名字）。测试：`tests/test_aggregate.py` 的 `test_feeds_are_named_by_the_config_not_by_the_directory_name`。

### B3. 批量审核（`batch.yml`）

管理页的待审列表里每条申请有个勾：“通过所选”“拒绝所选…”“选中检查通过的”“一键通过全部检查通过的”。这些**不是**给每个 issue 加 `approved` 标签：一次给几十个 issue 加标签会同时启动几十次 `Submissions` 运行，而 GitHub 的并发组里**只留下正在跑的那一个和最后排队的那一个**，中间的会被取消，留下“已批准却没处理”的申请。所以管理页只启动**一次** `batch.yml`（输入：`mode` = approve / reject、`numbers` = 最多 60 个 issue 编号、`reject` 时可带一句 `reason`），在这一次运行里：逐个读 issue（只处理仍然开着、属于待审队列、没有 `listed` / `rejected` 的，其余原样跳过）→ 和单个加标签时完全一样地检查、登记、抓取 → **只构建、提交一次** → 逐个在 issue 里回复、改标签、关闭。输入只通过环境变量进入，由 `aggregate.py batch` 校验（编号只能是数字和逗号，最多 60 个），管理接口的白名单也同样校验。命令行：`python aggregate.py batch --mode approve --numbers 12,13,14 --out results.json` 然后 `python aggregate.py apply --result results.json`（需要 `GITHUB_TOKEN` 和 `GITHUB_REPOSITORY`）。“一键通过”只包含机器人检查通过的；疑似重复的要自己勾。

### B4. 热榜服务（`api/hot.py`，不用任何设置就能用）

“热榜网站”做的事：向平台**自己网页用的那个小接口**（和你在浏览器里打开那个榜单页时发出的请求一样，带浏览器的请求头）要一份 JSON（或网页），取出名次、标题、热度和链接，几分钟缓存一下再转给你；**不读、不存任何文章正文**。主站的“公共目录 → 热榜”页读的就是它（六栏、每栏几个平台，点开的内容尽量在站内显示，见下面“站内显示”）；同一份榜单也是一份普通的 RSS，所以读者点“订阅这个榜单”就能把它放进书架，之后每次更新就是平台当前的榜单：条目标题是榜上的标题，说明里写“第 N 名 · 热度”。和提交服务、更新服务在同一个 Vercel 项目里，部署完就有。

- `GET /api/hot` 返回现在有哪些栏、哪些平台（JSON，允许任何页面读取：`columns`，以及每个平台的 `id`、`name`、`name_en`、`home`、`lang`、`col`、`ranked`，还可能有 `via`（榜单其实来自哪里）和 `read`（点开的是文章，页面可以在阅读器里读））；`GET /api/hot?p=github&n=30` 返回那一个榜的 RSS，`&format=json` 是 JSON（多一个 `updated`，服务读到它的时间）。结果在每个暖实例里缓存 3 分钟，CDN 缓存 10 分钟，平台读不到时最长一小时内用上一份。
- **六栏、五十多个平台，全部默认开启**（站长用 `HOT_DISABLE` 关）：
  - **综合热点**：微博热搜、知乎热榜、抖音热点、小红书热门（经 rebang.today）、B 站热门视频、公众号热文（经 tophub.today）、今日头条热榜、虎扑步行街（经 tophub.today）、知乎日报；
  - **科技与开发**：GitHub Trending、掘金、V2EX、Hacker News、Lobsters、DEV 社区、Product Hunt、少数派、极客公园、IT 之家、博客园、36 氪 24 小时热榜（经 tophub.today）；
  - **财经**：雪球热帖（经 tophub.today）、同花顺热股、华尔街见闻、CNBC、Bloomberg Markets、CoinGecko 加密货币热搜；
  - **国际**：Google 热搜（一个榜，按国家）、维基百科阅读榜（英文 / 中文）、BBC 国际、BBC 中文、法广中文、半岛电视台、卫报国际、Mastodon 热门链接；
  - **AI 与学术**：Hugging Face 每日论文 / 热门模型、arXiv cs.AI / cs.CL、OpenAI 动态、量子位、MIT Technology Review AI、TechCrunch AI、Nature、Science、Phys.org；
  - **读书与影音**：微信读书飙升榜 / **小说榜**、**起点中文网 / 起点女生网 / 纵横中文网 / 推书君的网文榜**（经 tophub.today）、Open Library 近期热门、Project Gutenberg 热门公版书、网易云音乐飙升榜、QQ 音乐飙升榜、Apple 播客榜（中国 / 美国）、Apple Music 最多播放、Steam 热销、TapTap 热门游戏和豆瓣新片榜（经 tophub.today）、YouTube 热门（要 key，见下）。
- **后加的英文来源**（都逐个试过：文章能在站内阅读器里读出正文）：科技与开发 + The Verge、Ars Technica、TechCrunch、Hackaday、Slashdot；国际 + DW 德国之声（英文）、France 24（英文）、The Conversation；AI 与学术 + Quanta 杂志、科学美国人、新科学家；读书与影音 + Longreads、Psyche、Pitchfork、Variety、Letterboxd 影评、Polygon。它们是订阅源，没有名次，页面按时间排。没试通的（NPR、Atlas Obscura、Kottke、IGN、BBC 英文版的页面读不出正文，Reddit / WSJ / FT 不让读）没加；BBC 英文、Bloomberg、OpenAI、Science 这几个页面读不出正文，点开显示订阅源自带的摘要。
- **站内显示（`hotdetail.py`）**：点一行不再把读者送走，能显示的在页面里显示——B 站、YouTube、网易云的视频 / 歌曲用平台**自己允许别的网站嵌入的播放器**在窗口里播（`player.bilibili.com`、`youtube-nocookie.com`、`music.163.com/outchain`，页面里的 `embedOf` 按地址认）；V2EX 的主题（正文加回复）、知乎日报的文章、GitHub 仓库的 README、Apple 播客榜的节目（列出最新几集，点一集就在播放条里播）由服务取回文字，在阅读器里显示；微博、抖音、知乎、今日头条、Google 热搜这类“一行只是一句话”的，显示一张卡片：标题、热度、平台给的摘要和图，再加**这句话相关的新闻**（来自 Bing 新闻的 RSS，每条能点开在阅读器里读）。接口：`GET /api/hot?detail=<那一行的地址>` 返回 `{"ok", "kind": "html"|"md"|"podcast", "title", "content"（已清理的 html）或 "md" 加 "base"，或 "feed"（播客的订阅源）, "image", "meta"}`；`GET /api/hot?related=<这句话>&lang=zh|en` 返回 `{"items": [{"title","url","source","date","snippet"}]}`。请求选不了主机：只有 V2EX 主题、知乎日报文章、GitHub 仓库、Apple 播客这几种地址形状会被取，别的一律 404，什么也不取。榜单的每一行也可能带 `img`（封面）和 `body`（平台给的摘要）。`HOT_DETAIL=0` 关掉前一个，`HOT_RELATED=0` 关掉后一个。没有页面能显示的平台（微博 / 抖音 / 小红书的正文、知乎的回答、公众号、QQ 音乐）平台自己不让，也不能转载，所以卡片的最下面始终有“去原站看”。
- **榜单从哪来**：平台自己的网页接口（微博、知乎、抖音、B 站、掘金、V2EX、GitHub、Hacker News、网易云、QQ 音乐、微信读书、同花顺……）；**平台自己的 RSS / Atom**（新闻、博客、arXiv……）——订阅源没有名次，页面按时间排、写日期；**tophub.today / rebang.today** 这两个已经在整理榜单的网站，用来补没有公开接口的几个（公众号、雪球、小红书、起点等网文榜、虎扑、36 氪、TapTap、豆瓣新片），页面上会写“数据来自 …”；它们对同一个地址的请求有限制，所以这几个榜在每个暖实例里 10 分钟才读一次（别的 3 分钟），读不到时用最长一小时内的上一份；站长自己加的（下面）。
- **按国家的榜（YouTube 热门、Google 热搜）跟着读者走**：请求可以带 `&region=JP`（两个字母的国家 / 地区代码）；页面一开始请求的是 `&region=auto`，服务按这个顺序决定：① **读者访问地址所在的国家**（Vercel 给函数的请求头 `x-vercel-ip-country`，只用于这一次回答，不保存、不记录）；② **浏览器语言里的国家**（`Accept-Language`：`ja-JP` → 日本、`zh-TW` → 台湾、`de-AT` → 奥地利，只有语言的 `fr` / `ko` / `ja` 等按常见国家算）；③ 站长设的 `YOUTUBE_REGION`；④ 美国。这个国家 YouTube / Google 没有榜时（比如中国大陆）顺延到下一条。读者在页面上的“地区”下拉里自己选了就以选的为准（只记在这个浏览器里），订阅这个榜单时订阅的是当时显示的那个国家（订阅地址里带 `&region=…`，不会跟着读者变）。依赖读者地址 / 语言的回答带 `Cache-Control: private`，CDN 不会拿给别人；明确写了国家的回答照常缓存。每个国家各缓存各的（3 分钟）。国家名由浏览器按页面语言显示。
- **要 key 的榜（YouTube 热门）怎么配**：
  1. 打开 <https://console.cloud.google.com/>，新建（或选）一个项目；
  2. “API 和服务 → 库”，搜 **YouTube Data API v3**，点“启用”；
  3. “API 和服务 → 凭据 → 创建凭据 → API 密钥”，复制那串密钥；建议点“限制密钥”→“API 限制”→只勾 YouTube Data API v3（Vercel 的出口地址不固定，不要做 IP 限制）；
  4. 打开 Vercel 的 shelf-directory 项目 → Settings → Environment Variables，添加 `YOUTUBE_API_KEY`（值就是那串密钥，勾上 Production），可选再加 `YOUTUBE_REGION`（国家都判断不出来时用的默认国家，不写是 `US`）；
  5. 到 Deployments 里对最新的一次部署点“Redeploy”（环境变量要重新部署才生效）。
  打开 `https://你的目录站点/api/hot`，出现了 `youtube` 就好了；没设 key 这个榜整个不存在（不是报错）；key 只在服务端的请求里用，不会出现在任何回答或错误里。配额：每天默认 1 万点，一次读取 1 点，3 分钟内同一个国家的重复读取走缓存，所以够用。
- **站长自己加榜**：`registry/hot.json`，改完重新部署。三种写法，`col` 是放进哪一栏（`news`、`tech`、`money`、`world`、`ai`、`play`，不写放第一栏），`ranked: true` 表示订阅源的顺序就是名次（RSSHub 的热榜），`read: false` 表示点开的不是文章：
  ```json
  {"platforms": [
    {"id": "my-blog", "name": "我关注的博客", "col": "tech", "feed": "https://example.com/feed.xml"},
    {"id": "x-trends", "name": "X 热门话题", "col": "world", "rsshub": "/twitter/trends", "ranked": true},
    {"id": "qidian", "name": "起点月票榜", "col": "play", "tophub": "节点编号"}
  ]}
  ```
  `feed` 是任何 RSS / Atom 地址；`rsshub` 是 RSSHub 的路由，**要先设环境变量 `RSSHUB_BASE=https://你自己的-rsshub-地址`**（没设就不显示；公共 RSSHub 实例常被限流，最好自己部署一个）——没有公开榜单接口的平台（X、Reddit、微博话题……）可以这样经 RSSHub 转成榜单；`tophub` 是 tophub.today 上那个榜的节点编号（榜单页地址 `https://tophub.today/n/…` 末尾那一段）。id 只能用小写字母、数字和连字符，不能和内置的重名；写错的一项被跳过，不影响别的。
- **关掉**：`HOT_DISABLE=weibo,douyin`（逗号分隔的榜单编号），关掉的返回 404，页面上也不出现；一栏里一个榜都不剩，这一栏也不出现。
- **这些是平台网页自己的接口，不是公开的、有文档的 API**：可能随时改格式或关闭，使用条款以平台自己的为准；格式变了会返回 502 和原因，而不是一份空的订阅。国内平台的接口有时只肯回答国内的 IP：`vercel.json` 里已经设了 `"regions": ["hkg1"]`（香港节点）。实测香港节点仍读不到少数派、极客公园、博客园、同花顺、华尔街见闻、量子位、小红书（rebang）和知乎日报：Vercel 的香港出口解析不了 `news-at.zhihu.com`、连不上其余几个。`GET /api/hot?diag=zhihu_daily` 会告诉你某个榜在部署的机房里为什么不通（域名找不到 / 连不上，机房是哪个）；这几个榜暂时读不到的话，办法只有自己的 RSSHub（`registry/hot.json`，见上）或者一个国内能连的中转。没做的：懂车帝（`robots.txt` 明确禁止）、X / Reddit（要登录或付费接口，用上面的 RSSHub 办法）、晋江（页面是 GBK 加反爬；tophub 上没有它，可以经 RSSHub 自己加）、Z-Library（盗版库，不接入）。
- 安全：每个榜的地址写死在 `hotapi.py` 的 `PLATFORMS`（或站长的 `registry/hot.json`）里，请求能选的只有“哪个榜、几条”，不会读读者给的任何网址；读取走 `aggregate.py` 的受限抓取（只走 https、只连公网地址、限大小和时间）；每一行只收 http(s) 地址，标题当文本，去掉 `utm_*` / `at_*` 这类跟踪参数。
- 订阅它：主站页面的“订阅这个榜单”；`recommended.json` 里把条目的 `feed` 写成 `hot:github`（`hot:` 加榜单编号），页面会换成这个服务的地址；也可以直接订阅 `https://你的目录站点/api/hot?p=github`。测试：`tests/test_hot.py`、`tests/test_hotdetail.py`、`tests/test_translate.py`（不联网）。
- **公共目录里固定的热榜平台**：`registry/shelves.json` 里 `hot-zhihu`、`hot-weibo`、`hot-bilibili`、`hot-github`、`hot-hackernews`、`hot-netease`、`hot-youtube` 等 18 个书架（`status: fixed`，各带 `platform`）读的就是这个服务的 `…/api/hot?n=10&p=…`，每天跟着目录更新一次，各取前 10 名。主站“全部 → 平台”标签里它们是固定的（总在、按条数排），别的订阅是动态的（最近最热的 5 个，其余在“更多”里）；平台标签下有“看实时热榜 ›”一键到热榜页。点卡片和点热榜的一行一样，在站内显示。新增 / 去掉一个：`python aggregate.py approve …` / `remove …`，或者改 `registry/shelves.json`。

### B4b. 英文标题的中文小字（`translate.py`，可选，没设 key 就什么都不发生）

页面是中文时，英文标题下面可以有一行灰色的小字中文（`title_zh`）。**只发标题**，不发任何读者读了什么、做了什么。用哪家翻译由环境变量决定，按这个顺序用第一个设了的：

| 环境变量 | 说明 |
|---|---|
| `DEEPL_API_KEY` | DeepL。免费版的 key 以 `:fx` 结尾（`api-free.deepl.com`，每月 50 万字符），付费版走 `api.deepl.com`。**首选** |
| `GOOGLE_TRANSLATE_KEY` | Google Cloud Translation（基础版 v2）。备用 |
| `LIBRETRANSLATE_URL`（+ `LIBRETRANSLATE_KEY`） | 自己部署的 LibreTranslate（必须 https）。备用 |

- **目录里的卡片**：每天的构建（`daily.yml`）把还没有中文的英文标题（最新的先，每次最多 300 条）发去翻译，结果记在 `registry/titles_zh.json`（提交进仓库，同一个标题只翻一次），`directory.json` 的条目里多一个 `title_zh`。key 要设成**仓库的 Secret**（GitHub → Settings → Secrets and variables → Actions → New repository secret，名字就是上表里那个）；`daily.yml` 已经把它们传给构建，没设的是空的，等于没有。
- **热榜页**：页面是中文、榜单是英文时请求 `…&format=json&tr=zh`，服务把这一榜的标题翻一遍（先查 `registry/titles_zh.json`，新的在这个实例的内存里记着，不写文件，一次最多 60 条）。key 要设在 **Vercel 的环境变量**里（和 `YOUTUBE_API_KEY` 同一个地方），然后 Redeploy。
- 翻译失败（key 错了、额度用完、对方不通）和没有 key 一样：页面照常显示英文标题，不报错。翻译结果必须含中文才会采用，原样返回的丢掉。
- 页面只在“页面是中文、条目不是中文”时才显示这行小字；英文页面不显示。测试：`tests/test_translate.py`。

### B4c. 今日听什么（`api/listen.py`，不设 key 也有内容）

主站公共空间侧栏的“今日听什么”（和发现页上的一小块）读的就是它。**每天换一批**：中国时间的日期是一个固定洗牌的种子（也决定用哪个国家的榜），所以同一天所有人看到的一样，第二天全换；服务不存任何东西，每个来源的回答在实例内存里留 6 小时，CDN 留 30 分钟。

- `GET /api/listen[?lang=zh|en]` → 今天有哪些榜单 / 歌单 / 风格 / 播客榜 / 播客分类（不带内容）。每项有 `type`（`music` 或 `podcast`，页面据此把音乐和播客分开）和 `kind`：`daily` 每日推荐、`chart` 榜单（华语 / 欧美 / 韩流 / 日系热歌；各国播客榜）、`list` 歌单（Deezer 全球 / 美国 / 英国 Top 100，Deezer 和 Audius 此刻最热的歌单，网易云的 13 个官方榜）、`style` 风格或播客分类（音乐：摇滚、流行、说唱、电子、舞曲、R&B、乡村、另类、金属、爵士、古典、民谣、拉丁、雷鬼、蓝调、放松、专注、助眠、ACG、音乐视频；播客：新闻、商业、故事、英语学习、科技、社会与文化、教育、喜剧、历史、健康、科学、艺术与影视）。
- **`lang` 是读者的界面语言**（页面按系统语言自动带上，读者点一下“中 / EN”按钮就换）。读者语言的榜单排在前面（“热门播客”和每日推荐对谁都排在最前），每日推荐和部分榜单按语言换来源：英文读者的每日推荐是欧美、英语歌，不是华语歌。没带 `lang` 当中文。
- `GET /api/listen?g=rock&lang=en` → 一个榜单的内容：每个榜单**缓存 200 项**（Deezer 的歌单是它自己的 100 首，每日推荐 100 首，Audius 的歌单是它自己的长度），页面一次只画 30 项，滚到底再自动画 30 项；每首带一个页面自己的播放条能放的**声音地址**（整首，或平台公开的 30 秒试听）；播客是**整集**。榜单用到的来源同时去问，**6 秒内没回答的来源这次就不要了**（Vercel 函数只有 10 秒）；一个来源有多个时一轮取每个来源的一首，同一首歌不出现两次，同一位歌手最多 2 首（每日推荐 1 首）。
- `GET /api/listen?diag=1` → 每个来源读到没有、读到几首（不含任何秘密）。

| 来源 | 内容 | 在站内播放条里放什么 | 要 key 吗 |
|---|---|---|---|
| **Deezer** | 全球榜、10 种风格榜、热门歌单、国家 Top 100 歌单 | 公开的 30 秒试听（行上写“30 秒试听”） | 不要 |
| **Apple Music** | 各国“最多播放”榜（中国大陆、台湾、香港、美国、英国、日本、韩国……，每个榜 200 首）和各风格榜 | 公开的 30 秒试听，行尾 ↗ 去 Apple Music 听全曲 | 不要 |
| **Apple Podcasts** | 各国播客榜（中国大陆、台湾、香港、美国、英国、日本）和 12 个播客分类榜，每个 200 个节目；每个节目取它**最新一集** | **整集**（节目自己服务器上的音频文件，`episodeUrl`；不经过本站，也不下载） | 不要 |
| **Audius** | 各风格本周趋势、热门歌单 | **整首**（艺术家放在 Audius 上让任何人收听的） | 不要 |
| **Jamendo** | 免费音乐（Creative Commons） | **整首** | `JAMENDO_CLIENT_ID` |
| **网易云音乐** | 13 个官方榜 | **整首**（直连，默认关，见下） | 不要 |
| **YouTube** | 音乐分类热门视频 | 不放进播放条，只能在窗口里用 YouTube 自己的播放器 | `YOUTUBE_API_KEY` |

“华语热歌”先用网易云（开了直连时），不够的用 Apple 中国大陆、台湾、香港的榜补到 30 首；没有直连时只有 Apple 的试听。

- **Deezer / Apple 的试听是 30 秒**，不是全曲；只有 Audius、Jamendo（和开了直连的网易云）是整首。使用条款以各平台的为准（署名、导向平台）。中国大陆的 Apple 店没有分风格榜，所以华语用台湾 / 香港的。
- **Jamendo 怎么配**：到 Jamendo 开发者网站（<https://developer.jamendo.com/>）注册一个应用，拿到 `client_id`；Vercel 的 shelf-directory 项目 → Settings → Environment Variables 添加 `JAMENDO_CLIENT_ID` → Redeploy。每个实例每 6 小时每种风格请求一次，远低于免费额度（第三方资料写的是每月 3.5 万次，以 Jamendo 官方条款为准）。**授权**：每首歌有自己的 Creative Commons 授权，本站有广告，所以**带“非商业”（NC）的歌默认不放**，授权不明的也不放（`LISTEN_ALLOW_NC=1` 才放开，不建议）；授权名在“正在播放”页和行上。Jamendo 的开发者文档写着“为直接或间接的商业活动使用其音乐需购买商用授权”（这句在电台接口的说明里），一个带广告的页面是否算在内，请自己向 Jamendo 确认。
- **网易云“站内播放整首”（`LISTEN_NETEASE_DIRECT=1`，默认关；关着时网易云的榜单整个不出现）**：网易云没有给第三方的正式取歌接口。打开后，服务对榜上前 40 首逐一问 `music.163.com/song/media/outer/url?id=…`（只看它是不是给出跳转到网易云声音服务器的地址，不下载），能放的变成站内播放条的歌，不能放的（VIP、没有版权，实测约三成）去掉。实测（2026-10-29，中国大陆网络）：飙升榜前 20 首里 14 首能放，在 https 页面的 `<audio>` 里整首能播。**这个地址不是文档里的接口，随时可能改；歌是版权方的，在自己的播放器里放整首歌并没有得到版权方的授权，风险由站长自己承担**——所以默认关着。
- **为什么不用 Spotify**：2026 年 2 月起 Spotify 的开发模式限制很多——应用所有者要有 Premium、最多 5 个用户、官方定位是个人非商业使用，新发行、分类、艺人热门歌曲等接口已被移除，搜索一次最多 10 条，别人歌单里的歌读不到；30 秒试听地址（`preview_url`）从 2024 年 11 月起对新建的应用基本拿不到；整首要在自己的播放器里放，只有 Web Playback SDK 一条路，听的人都得有 Premium 并登录。所以这里不接 Spotify；你的 Spotify key 到底能拿到什么，用下面的自检一试便知。
- **YouTube**：只能用官方嵌入播放器（页面用 `youtube-nocookie.com`），不能只取声音，所以“音乐视频”一组的条目 `mode` 是 `embed`，点开在窗口里放。YouTube 在中国大陆通常打不开，这一组带 `net: global`，页面标“海外”。
- **播客**：服务读 Apple Podcasts 的榜单（`toppodcasts`，各国总榜和分类榜），再用 iTunes 的查询接口（每批 50 个节目）取每个节目最新一集的音频地址，所以榜上每一项都是“某节目的最新一集”，在站内播放条里整集播放（长度一般 20–60 分钟，行上写节目名和日期）。页面“播客”一侧还有“目录里的节目”：公共目录里每个播客节目的 RSS 条目（`audio`），每个节目一个标签，最新一集排前面。榜上没有的节目（比如某个只在小宇宙、喜马拉雅发的节目）读不到——要加就把它的 RSS 地址交给公共目录。
- **自己加榜单**：新建 `registry/listen.json`（写错的条目被跳过，别的照常）：
  ```json
  {"groups": [
    {"id": "x-punk", "name": "朋克", "name_en": "Punk", "kind": "style",
     "sources": [["deezer", 152], ["audius", "Punk"], ["apple_genre", "us", 21]]}
  ]}
  ```
  `id` 必须以 `x-` 开头；`kind` 是 `chart`、`list` 或 `style`；一个来源是 `[种类, 参数]`：`["deezer", 风格编号]`（0 是全部，152 摇滚，116 说唱，132 流行，106 电子，129 爵士，98 古典，84 乡村，165 R&B，85 另类，464 金属，466 民谣，197 拉丁，144 雷鬼，153 蓝调，113 舞曲，173 影视游戏）、`["deezer_pl", 歌单编号]`、`["audius", "Rock"]`（Audius 的风格名；空字符串是全部）、`["audius_pl", 歌单编号]`、`["apple", "us"]`（国家：cn us jp kr tw hk gb de fr）、`["apple_genre", "us", 21]`（Apple 的风格编号：14 流行、21 摇滚、18 说唱、20 另类、15 R&B、6 乡村、7 电子、17 舞曲、11 爵士、5 古典、1289 民谣、12 拉丁、24 雷鬼、2 蓝调、27 J-Pop、29 动漫、53 器乐、13 新世纪）、`["jamendo", "chill"]`（chill focus sleep jazz classical acoustic）、`["netease", 榜单编号]`。
- **关掉**：`LISTEN_DISABLE=metal,blues`（榜单编号，逗号分隔）；`LISTEN=0` 整个功能关掉（404，页面上不出现）。一个榜单的来源没 key / 没开 / 读不到，它就少一个来源，全都不行这个榜单就不出现。
- **自检（给你“能不能用”的答案）**：打开 `https://你的目录站点/api/listen?diag=1`。**部署前就能在自己电脑上测**：在这个目录里开 PowerShell，设好钥匙（只在这个窗口里有效）再运行 `python -X utf8 listen.py`：
  ```powershell
  $env:JAMENDO_CLIENT_ID = '你的 client_id'
  $env:YOUTUBE_API_KEY = '你的 YouTube key'
  $env:SPOTIFY_CLIENT_ID = '…'       # 这两个只用于自检：服务本身不用 Spotify 的 API
  $env:SPOTIFY_CLIENT_SECRET = '…'
  python -X utf8 listen.py
  ```
  它会逐项说：每把钥匙**有没有权限、为什么没有**（YouTube 会说是密钥不对、项目里没启用 YouTube Data API v3、配额用完了还是密钥做了限制；Spotify 会说能不能换到令牌、搜索能拿到几首、有没有试听地址、“新发行”接口是否被拒）、几个代表性榜单（每日推荐、华语、欧美、摇滚、说唱、古典、放松）的每个来源读到几首，并且**对每个提供者的一首歌真的取一小段声音**（`Range: bytes=0-1023`），告诉你从你的网络能不能取到。钥匙不会被打印出来。
  再加 `--browser`（`python -X utf8 listen.py --browser`）：在本机开一个小页面并打开你的浏览器，**每个提供者的前 2 首歌真的用 `<audio>` 放一下**（和站内播放条同一种方式，用你的网络和浏览器），✓ 是真的放出声音了，✗ 写着原因；有 YouTube 的话再放一个嵌入视频给你看。
- 安全：每个来源的地址写死在 `listen.py` 里，请求不能带任何网址（`?g=` 只认今天有的榜单编号）；站内播放的声音地址只收 Jamendo 的 `jamendo.com`、Apple 的 `audio-ssl.itunes.apple.com`、Deezer 的 `*.dzcdn.net`、Audius 的 `api.audius.co/v1/tracks/<编号>/stream` 和（开了直连时）网易云的 `music.163.com/song/media/outer/url?id=数字.mp3`（都是 https）；key 只在服务端的请求里用，不会出现在回答或错误里。测试：`tests/test_listen.py`（不联网）。

### B5. 匿名计数（`api/stat.py`，需要和同步一样的 Redis）

公共目录页上的“热度”“热门订阅 / 文章 Top 3”和“热度”排序，来自这里：只记**每条内容被打开、被收藏、被订阅了几次**，别的什么都不记。

- `POST /api/stat` `{"events":[{"t":"open|save|sub|like|dislike|unlike|undislike","k":"item|shelf","id":"…"}]}`（一次最多 20 个；打开、收藏、订阅页面每人每条每天只发一次；喜欢 / 不喜欢是“状态”，页面在本机记着，取消时发 `unlike` / `undislike`，计数减一但不会低于零，并让同一个人当天可以重新点）；`GET /api/stat` 返回最热的 400 条和 400 个书架 `{"items":{id:{o,s,u,l,d,h}},…}`（`o` 打开、`s` 收藏、`u` 订阅、`l` 喜欢、`d` 不喜欢的总数，`h` 热度 = 最近 7 天的 打开 ×1 + 喜欢 ×3 + 收藏 ×4 + 订阅 ×6，不喜欢只显示不参与排名），CDN 缓存 10 分钟。
- **不存什么**：不存地址、不用 Cookie、不记时间、不建账号。每条每种事件只有“一天一个数字”（保留 40 天）和“总数”。为了让同一个人对同一条一天只算一次，用（盐、当天日期、网络地址、事件类型、条目编号）做一个单向哈希放进 Redis，**两天后自动作废**，不能还原成地址，也没法把同一个人从今天跟到明天；另一个（盐、日期、地址）的哈希数一数这个人今天发了多少个事件，超过 `STAT_DAILY_EVENTS`（默认 200，最大 5000）就丢弃，防止一台机器刷热度。
- 页面在浏览器开了“请勿跟踪”或“全球隐私控制”时什么都不发；只统计目录里有的内容（编号要符合 `[A-Za-z0-9._-]{1,64}`）。
- 需要的环境变量：和同步服务一样的 Redis（`UPSTASH_REDIS_REST_URL` + `UPSTASH_REDIS_REST_TOKEN`，或 `KV_REST_API_URL` + `KV_REST_API_TOKEN`）、`IP_SALT`、`ALLOWED_ORIGINS`（来源只认它）；没有 Redis 时 `GET` 说 `"configured": false`，页面就不显示热度。测试：`tests/test_stat.py`（19 个）。

### B6. 站长推荐和领域标签

- **站长推荐**：给一个已收录的书架标上推荐和一句话：`python aggregate.py feature 书架编号 --blurb "每周一篇，读完就能用"`（`--off` 取消；收录时也可以直接 `approve 网址 --featured --blurb …`）。标过的书架在 `directory.json` 里有 `featured: true` 和 `blurb`，主站目录页顶部的“站长推荐”和“合集”下拉框里的“站长推荐”用它。
- **领域标签页**（科技、编程、商业、金融、学术、读书、人文、供应链、音乐、生活、娱乐、教育、工具、新闻、游戏、健康、设计）：`directory.json` 里的 `topics`，一条内容（或它的书架）带有某个领域的任一标签就算这个领域，不分大小写。想改领域，在 `registry/topics.json` 写 `{"topics":[{"id":"tech","zh":"科技","en":"Tech","tags":["科技","tech",…]}]}`（`id` 小写字母、数字、`-`，最多 30 个领域、每个 60 个标签）；没有这个文件或里面一个有效的都没有，就用内置的十七个。“平台”标签页不需要任何设置，主站按网址判断（想手工指定，见 B7 的 `platform`）。

### B7. 来源状态（固定 / 动态 / 试读 / 暂停）

给每个已登记的来源标注几件事。**都是可选的**，不写就按默认，旧的 `registry/shelves.json` 不用改：

| 字段 | 取值 | 默认 | 意思 |
|---|---|---|---|
| `status` | `fixed` / `dynamic` / `trial` / `paused` | `fixed` | 固定 = 长期；动态 = 按热点进出（主站“平台”里独立网站按热度排前五个，其余收在“更多”）；试读 = 新来源，小范围观察；**暂停** = 保留登记和已读到的内容，但**不更新、不在目录里出现** |
| `rights` | `official-link` / `licensed` / `unknown` | `unknown` | 官方订阅源或接口，只用标题、摘要和链接 / 作者授权 / 还没有确认 |
| `topics` | 领域编号的列表（`directory.json` 的 `topics[].id`） | 不写 | 手工指定属于哪些领域；写了，主站就不再靠标签猜 |
| `platform` | 平台编号：`zhihu`、`youtube`、`bilibili`、`wechat`……（小写字母、数字、`-`，最多 30 个字符） | 不写 | 手工指定平台；写了，主站就不再按网址猜 |
| `note` | 文字，最多 200 字 | 不写 | 给维护者看的备注。**不会写进 `docs/` 里的任何文件**，但它在 `registry/shelves.json` 里：仓库是公开的话，别人在 GitHub 上看得到，不要写机密 |

- **命令**：`python aggregate.py set 编号 [--status S] [--rights R] [--topics a,b] [--platform P] [--note 文字]`（没给的不动；`--topics=` `--platform=` `--note=` 写空就是清掉；值不对会拒绝并说明，什么都不写；改完马上重新生成 `docs/`）。`python aggregate.py shelves` 列出所有来源：编号、名称、状态、版权、领域、平台、篇数。收录时也可以直接带上：`approve 网址 --status trial --topics tech,dev`。
- **暂停**：`fetch` 和 `run` 跳过它（`fetch --only 编号` 仍可单独读一次）；`build` 里它不在 `shelves` 和 `items` 里，也不在 `feed/…` 的任何订阅源和 `shelves.opml` 里；快照文件保留，恢复（`set 编号 --status fixed`）后立刻回到目录，并在下一次读取时更新。
- **`build` 不会因为一个不对的值失败**：登记表里不合格的值被忽略并在日志里说明（`build: 编号: … (ignored)`），一个领域列表里对的留下、不对的去掉。
- **`directory.json` 里新增**：每个 `shelves[]` 一定有 `status` 和 `rights`，写了才有 `topics` 和 `platform`（`note` 永远没有）；顶层有整数 `paused`（暂停了几个）。**注意**：`shelves[].status` 以前表示“上一次读取是否成功”（`ok` / `error`），现在改叫 **`health`**，`status` 是上面的状态；页面没有读过旧的 `status`。社区投稿那个书架的 `rights` 是 `licensed`（作者提交时已经同意）。
- **栏目（`columns`）**：主站首页上的栏目由 `registry/config.json` 的 `columns` 决定，原样写进 `directory.json`（没写就没有这一项，页面用内置的）。每项 `{"id","zh","en","topics","platforms","types"}`：`id` 是 1–20 个小写字母、数字、`-`；`zh`、`en` 不能空、最多 24 个字；`topics` 是领域编号、`platforms` 是平台编号、`types` 取自 `article note video audio pdf tool book`，后三个可以不写；最多 12 项，不合格的整项丢掉并在日志里说明。一篇内容只属于一个栏目：页面先看 `platforms` / `types`，再看领域；这个归属由页面做，这里只检查和原样写出。仓库里默认写了八个栏目（今日摸鱼、AI 与科技、学习与研究、财经与商业、职场与产品、视频与创作者、读书与深度、全球正在发生）。
- **在管理页里改**：“来源”标签页（见 C）。它启动 `.github/workflows/set.yml`（`id` `status` `rights` `topics` `platform` `note` 六个输入，必须全给；空的 `topics` / `platform` / `note` 就是清掉），工作流里 `set` 之后，只要不是暂停就把这个来源重新读一次再生成目录，然后提交。

### C. 管理页

打开 Vercel 项目的 `/admin/`（例如 `https://adminbook.zhitree.cn/admin/`）。有两种登录方式：

**1. 输入密码（推荐）。** GitHub 令牌放在服务器的环境变量里，浏览器里从来没有它。在 Vercel 项目里再加两个环境变量，然后 `vercel --prod` 重新部署一次：

| 名字 | 值 |
|---|---|
| `ADMIN_PASSWORD` | 管理密码，**至少 12 个字符**（最好是一句长长的话；不到 12 个字符时管理密码功能不会启用）。 |
| `ADMIN_GITHUB_TOKEN` | 一个 fine-grained 令牌：只选这个仓库，**Issues: Read and write**、**Actions: Read and write**、**Contents: Read-only**。不要和访客提交用的 `SUBMIT_GITHUB_TOKEN` 共用（那一个只能写 issue）。 |

之后管理页就只出现一个密码框。密码在服务器（`api/admin.py`）上核对，登录后浏览器拿到的是一个**会过期的通行证**（不勾“记住”：关闭标签页或 8 小时；勾了：这台设备上 14 天），不是令牌。这个通行证只能换到管理页上要做的几件事：读申请、已收录的目录和登记的来源（`docs/directory.json`、`registry/shelves.json`，别的文件读不了）、给申请标 `approved` / `rejected`、写一条不超过 600 字的评论、启动 `remove.yml`（带一个编号）、`set.yml`（六个输入，每个都按格式检查）、`batch.yml` 和 `daily.yml`；别的一律在问 GitHub 之前就拒绝（读不了别的文件、改不了仓库设置、删不了 issue）。

- 输错密码要等 1 秒；同一个来源 15 分钟内输错 6 次就先锁住。这个计数只在一台服务器实例里，所以真正的防线是**密码够长**。
- **换密码或换令牌会让所有已登录的设备立刻退出**（通行证是用它们两个一起签名的）。手机丢了、怀疑泄露：换一下 `ADMIN_PASSWORD` 重新部署。
- 页面和这个函数在同一个站点，不开放跨站调用；页面里“改用 GitHub 令牌登录（高级）”仍然可用，但这种方式下令牌不会被记在浏览器里。

**2. 用 GitHub 令牌直接登录（没配密码时的样子）。** 页面要贴一个 GitHub 令牌（页面上有取法）：fine-grained，只选这个仓库，**Issues: Read and write** 和 **Actions: Read and write**。令牌只存在你的浏览器里（默认关闭标签页就忘掉，可以勾选“记住”），页面直接调 GitHub 的接口，没有任何后台。部署在 GitHub Pages 上的管理页（没有函数）只能用这种方式。

- **待审**：每个申请显示识别结果（机器人的回复）、检查状态（通过 / 未通过 / 重复）；文章投稿可以看全文预览。「通过」「拒绝…」（可写理由，只记在 issue 里，不会告诉申请人）。通过后等 Action 登记，页面会告诉你结果。
- **已收录**：所有书架，每个有「删除」；托管文章单独列出，每篇有「删除」。「立即更新目录」会马上重读所有来源。
- **来源**：登记的每个来源一张卡：状态（固定 / 动态 / 试读 / 暂停）、版权、领域（多选）、平台、备注；「保存」，以及一键「暂停…」「恢复」。暂停的来源也在这里（它不在“已收录”里）。保存由 `set.yml` 执行，约 1 分钟生效；这一页需要读 `registry/shelves.json` 的权限（密码方式服务器上的令牌有 Contents: Read-only；令牌方式要加上这一项）。
- **已处理**：最近通过和拒绝的申请。

## 日常

- **收到申请**：打开管理页处理；也可以仍然在 GitHub 的 issue 里给申请加标签 `approved` / `rejected`，效果一样。
- **每天自动**：重读所有来源，只有内容变了才提交。某个来源连续 7 天读不到会暂时从目录隐藏，恢复后自动回来。
- **下架**：管理页「删除」；或 `python aggregate.py remove 编号`（托管文章用 `post:编号`）后推送。想永久拒绝某个站点，写进 `registry/blocklist.txt`；想拦掉某些词，写进 `registry/blockwords.txt`（每行一个，文章里出现就拒绝）。
- **标签**：收录时申请人可以填，也会从来源自动推断。近义词在 `registry/tags.json` 里归并。

## 三种东西：网址、文章、书籍

| 提交的是 | 需要网址吗 | 通过审核后 | 检查什么 |
|---|---|---|---|
| 网址（博客、站点、仓库、单篇、视频、PDF） | 是 | 登记成书架 / 单条，每天自动更新 | 网址是否重复、是否安全；读出来的名称和简介与已有的是否很像 |
| 文章（Markdown / TXT 正文） | 否 | 目录托管成静态页面（`posts/` → `docs/p/`） | 正文内容哈希；标题 + 作者、开头 160 字是否与已有的很像 |
| 书籍（一本书、一张书单、一部在线读的书、一部小说，或 PDF / HTML / 工具：只有标题、作者、摘要、标签，网址可选） | 否 | 同上，托管一个只有这些文字的页面，类型 `book` | 标题 + 作者、摘要是否与已有的很像；填了网址时也查网址 |

书籍在仓库里就是一篇 `posts/*.md`（front matter 里 `kind: record`，正文是摘要），所以下架、拒绝、管理页的删除都和文章一样。issue 的表单字段是：标题、作者、标签、摘要、网址（可选）、页数（可选）、目录（可选，一行一条标题）、权利确认；页数和目录只在主站从书架提交时自动带上，存在书籍页面里显示，也能被目录搜索到。**四种形式用标签区分**（页面的提交窗口里点一下就加上）：`书单`（摘要里写书名，一书一行或用顿号隔开）、`在线书籍`（网址指向可以在线读的书：公版书、开放协议的书，页面在站内阅读器里读它）、`小说`（原创小说：长篇一章一章用“写文章”托管成页面，每章一篇、标签带 `小说`，读者在站内读；也可以用书籍卡片附上自己的连载网址），不带这些标签的就是一本普通的书。**只托管作者自己写的和公版 / 开放协议的作品**，别人有版权的书不托管、不转载，卡片只能是书名、作者、摘要。

**很像（疑似重复）怎么判：** 摘要的开头 160 字几乎一样（3 字片段重合度 ≥ 0.8）；或标题相同且作者相同；作者有一方没填时，标题相同还要摘要不矛盾；作者不同算两本不同的书；“首页”“README”“Home”这类标题和 4 字以下的标题不算。网址型的和已有**书架**比名称和简介，单篇和已有**条目**比标题和摘要。

- 访客在网页上（提交函数）：很像就直接拦下，告诉他像哪一个；
- 走 GitHub 申请表或绕过了网页的：issue 打上 `similar` 标签（管理页显示“疑似重复”），**维护者仍然可以通过**；`duplicate`（同一个网址）则不能通过。

**读不到的页面：** 对方不让程序访问（401 / 403 / 429 / 451）、对方出错或很慢（5xx、超时）、robots.txt 不允许、页面里读不出标题（要登录、靠脚本显示），这些不说明链接本身有问题：只要地址是安全的（https、公网、没有账号密码），就按链接登记（标题按网址猜），并提醒维护者手动看一眼；每天的更新读不到时保留原样，不算失败。404 / 410（页面没了）和不安全的地址仍然拒绝。**博客的子目录**（`example.com/blog/`）只当作一个链接：它的订阅源如果就是已收录整站的订阅源，就不再被当成“整站重复”。

## 重复是怎么过滤的

1. **网址归一**：`http` / `https`、`www`、末尾斜杠、`index.html`、`utm_*` 等跟踪参数、参数顺序、`youtu.be/ID` 与 `youtube.com/watch?v=ID`、`github.com/o/r.git`，都算同一个。
2. **提交前后查三次**：网页在你敲网址时就用已加载的目录比对（已有就直接说，不让提交）；函数在“检查”和“提交”时各查一次（读仓库里最新的登记表 + 已发布的目录 + 待审队列）；Action 在 issue 打开时和通过时再查一次。查的内容：已在目录里（同一来源、同一篇、仓库或它的上层文件夹）、已在审核中、已被拒绝（只记哈希，不告诉理由）、同一篇文章（忽略空白和 Markdown 标记）。只有走 GitHub 申请表（绕过了网页和函数）的重复申请会先变成 issue，由 Action 贴上“重复”标签，管理页里“通过”按钮不可点。
3. **GitHub 仓库**：fork 直接拒绝并指出原仓库；同一个文件内容（git 的内容哈希）在一个或多个仓库里只留最早登记的那份。
4. **订阅源条目**：同一个链接出现在多个来源里只留一条。站点主人可以在订阅源里用 `_shelf.listed = false` 让某一条不进任何目录（本站的 `directory: false`）。

## 本机命令

需要 Python 3.9+，没有第三方依赖。

```bash
python aggregate.py probe https://example.com/blog      # 看看一个网址会被识别成什么（什么都不写）
python aggregate.py approve https://example.com/blog --tags 供应链,笔记   # 直接收录（加 --starter：同时在读者的“添加 → 订阅”里当作“试试这些”推荐，只对订阅源有效）
python aggregate.py fetch            # 读取所有来源
python aggregate.py build [--base http://127.0.0.1:8770/]   # 生成 docs/（托管文章的网址需要 base）
python aggregate.py run              # fetch + build
python aggregate.py serve            # 在 http://127.0.0.1:8770/ 预览 docs/（带 CORS，给本机的网页用）
python aggregate.py feature 编号 --blurb "一句话"     # 标成站长推荐（--off 取消）
python aggregate.py set 编号 --status paused        # 来源状态：fixed | dynamic | trial | paused；另有 --rights --topics --platform --note（B7）
python aggregate.py shelves          # 列出所有来源和它们的状态、版权、领域、平台、篇数
python aggregate.py remove 编号       # 或 post:文章编号
python -X utf8 -m unittest discover -s tests -v          # 测试（不联网）
```

本机试用整个流程：`python aggregate.py serve`，再把 `my-archive` 的 `site.json` 写成 `"directory": "http://127.0.0.1:8770/"`，运行 `python serve.py`。

## 它怎么保证安全

- 聚合器：只走 https:443，域名必须解析到公网地址（有代理时由代理解析，IP 字面量和 localhost 之类仍拒绝），重定向每一跳都重新检查；响应 ≤2 MB；每次请求 15 秒超时；XML 里有 DOCTYPE / ENTITY 的订阅源直接拒绝；网页只在 `robots.txt` 允许时读取。
- 提交函数：只有“检查”会读访客给的地址，用和聚合器同一套受限读取（见上）；令牌只能写 issue、读文件；来源页面必须在 `ALLOWED_ORIGINS` 里；每个发送者每天 5 条、待审队列最多 100 条；隐藏字段防机器人；不记录发送者的地址（只存加盐哈希）。
- 托管文章：Markdown 经 `mdsafe.py` 转成页面，其中一切都转义，**不含脚本**；链接只允许 http(s) 并带 `rel="nofollow ugc noopener noreferrer"`；**图片不内嵌**（只留链接）；超长行和过深嵌套有限制；页面 `noindex`。
- issue 里的文字只当数据读：由 Python 从事件文件里解析，从不拼进 shell 命令；机器人回复里的标题、链接都做了转义（不会 @ 到别人）。
- 管理页：没有后台，令牌只在你的浏览器里；页面带严格的内容安全策略（只允许自己的脚本和 GitHub 的接口）；issue 里的文字只用 textContent 或经 DOMPurify 后显示。
- 目录页面不含任何统计脚本；收录的链接在网页里带 `rel="nofollow ugc noopener"`。
- 许可：这个仓库没有附带许可证，你自己决定（建议 MIT 或 CC0）。
