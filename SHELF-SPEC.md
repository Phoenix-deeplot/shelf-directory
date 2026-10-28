# 书架规范（Shelf spec，第 1 版）

一个「书架」就是一个能被订阅的网站。公共目录只读它的订阅源，不需要账号、不需要上传。
这份文档说明：什么样的来源能被收录、目录读到什么、目录自己的数据长什么样。

## 1. 谁都能被收录

不需要用本项目的程序。下面任何一种都可以，**贴网址就行**：

| 来源 | 怎么收录 | 更新 |
|---|---|---|
| 有订阅源的网站、博客、播客、YouTube 频道 | 贴首页网址或订阅源地址 | 每天读一次 |
| GitHub 仓库，或其中的文件夹 | 贴 `github.com/用户/仓库` 或 `…/tree/分支/文件夹` | 每天读一次 |
| 单篇文章、视频、PDF | 贴那一篇的网址 | 不更新 |
| `.md` 文件、Gist | 贴文件或 Gist 的网址 | 不更新 |

支持的订阅源格式：JSON Feed 1.1、Atom 1.0、RSS 2.0、RSS 1.0。首页网址会自动找订阅源（页面里的 `<link rel="alternate">`，找不到再试 `/feed.xml` `/atom.xml` `/rss.xml` `/index.xml` `/feed` `/rss` `/feed.json`）。

## 2. 目录读到什么、不读什么

**只记元数据**：标题（≤140 字）、摘要（≤160 字）、封面图地址（只收 https）、日期、标签（每条 ≤8 个）、语言、类型，以及回到原文的链接。
**不复制正文**，不内嵌视频或播放器，点击一律去原站。

每次读取的上限：响应 ≤2 MB，一个来源 ≤500 条（订阅源只留最新 50 条，GitHub 仓库最多 500 个文件）。

读取方式：只走 https、只走 443 端口、域名必须解析到公网地址（内网、本机地址一律拒绝，重定向的每一跳都重新检查）；网页（不是订阅源）只在 `robots.txt` 允许时读取；读不到连续 7 次的来源暂时从目录里隐藏，恢复后自动回来。

条目类型：`article` 文章、`note` 笔记/文档、`video` 视频、`audio` 音频、`pdf`、`tool` 工具。类型从订阅源的附件、链接的域名或后缀推断；本项目的站点会在订阅源里直接写明（见下）。

标签的来源：订阅源条目的分类、GitHub 仓库的 topics、Markdown 的 `tags`、网页的 `article:tag` 和 `keywords`，加上维护者收录时补的标签。相同的标签（忽略大小写）合并，`registry/tags.json` 可以把近义词归成一个。

## 3. 用本项目程序的站点：`_shelf` 扩展

本项目生成的 `feed.json` 带一个很小的扩展（JSON Feed 允许以下划线开头的自定义字段），目录因此能知道得更准：

```json
{
  "version": "https://jsonfeed.org/version/1.1",
  "title": "…", "home_page_url": "…", "feed_url": "…",
  "_shelf": { "spec": 1, "license": "CC BY-NC 4.0" },
  "items": [
    { "id": "…", "url": "…", "title": "…", "content_text": "…", "tags": ["…"],
      "_shelf": { "type": "note", "category": "books" } }
  ]
}
```

- `_shelf.spec`：规范版本，现在是 `1`。
- `_shelf.license`（可选）：站点的许可说明，目录里会显示出来。在站点的 `site.json` 里写 `"license"` 即可。
- 条目的 `_shelf.type`：`note` / `tool` / `pdf` 等，取值同上面的类型；`_shelf.category` 是站内分类。
- 条目的书籍信息（都可选，对应站点转换后的 `doc.json`，只有这几项，**没有正文**）：标准字段 `authors[0].name`（或 `_shelf.author`）是作者，`_shelf.pages` 是页数（整数），`_shelf.toc` 是目录标题的列表（最多 40 条、每条最多 60 字）。目录把它们写进 `directory.json` 的条目（`author`、`pages`、`toc`），页面显示作者和页数，搜索可以按作者、目录里的词和页数找到；没有就没有这几个字段。
- 条目的 `_shelf.listed`：写 `false` 表示这一条留在你的站点和订阅源里，但请任何公共目录不要收录（本站在文章 front matter 里写 `directory: false`，或在卡片上点“不进公共目录”）。
- 订阅源条目的 `tags` 里如果带着 `_shelf.category` 的同名标签，目录会把它去掉（分类不是标签）。
- 没有这个扩展也完全可以被收录，目录会自己推断。

## 4. 公共目录的数据格式（`directory.json`）

目录每天重新生成，放在 `docs/` 里由 GitHub Pages 发布；网页的「公共目录」直接读它。

```json
{
  "spec": 1, "title": "公共目录", "repo": "用户/仓库", "submit_api": "https://…/api/submit（可为空）", "generated": "2026-10-14", "base": "https://…/", "count": 120, "paused": 0,
  "types": { "article": 90, "note": 20, "video": 10 },
  "shelves": [ { "id": "books-zhitree-cn", "name": "…", "description": "…", "home": "…", "kind": "feed|repo|link", "lang": "zh",
                 "license": "", "tags": [], "n": 6, "updated": "2026-10-13", "feed": "源订阅源地址（仅 feed 类）", "shelf_feed": "feed/shelf/books-zhitree-cn",
                 "health": "ok|error", "status": "fixed|dynamic|trial", "rights": "official-link|licensed|unknown" } ],
  "tags":   [ { "name": "供应链", "slug": "t-3f2a1c", "n": 5 } ],
  "feeds":  { "all": "feed/all", "type": "feed/type/{type}", "tag": "feed/tag/{slug}", "shelf": "feed/shelf/{id}" },
  "items":  [ { "id": "a1b2c3d4e5", "title": "…", "summary": "…", "url": "…", "image": "", "date": "2026-10-13",
                "type": "article", "lang": "zh", "tags": ["…"], "shelf": "books-zhitree-cn",
                "author": "（可选）作者", "pages": 242, "toc": ["（可选）目录标题", "…"] } ]
}
```

- `items` 按 `date` 从新到旧；没有自己日期的订阅源条目，`date` 是目录第一次看到它的那天；GitHub 仓库里没有日期的文件，`date` 为空，排在最后。
- 同一个链接出现在多个来源里只留一条（网址归一后比较：`http`/`https`、`www`、末尾斜杠、`index.html`、跟踪参数都不算区别）；GitHub 仓库里内容完全相同的文件（git 内容哈希）也只留一条，fork 不收录。
- 每个 `feeds` 模板加上 `.xml`（Atom）或 `.json`（JSON Feed）就是一个可以订阅的地址，只含最新 50 条。标签的 `slug` 是 ASCII：英文标签用它本身，中文标签是一个固定的短代码。
- `shelves.opml`：所有书架的订阅源清单，可一次导入阅读器。
- 目录主人对每个来源的标注：`status`（`fixed` 长期、`dynamic` 按热点进出、`trial` 试读；`paused` 的来源不出现在 `shelves`、`items`、任何订阅源和 `shelves.opml` 里，只计入顶层的 `paused`）、`rights`（官方订阅源或接口只用标题摘要链接 / 作者授权 / 未确认），以及可选的 `topics`（领域编号）和 `platform`（平台编号，写了就不必再按网址猜）。`health` 是上一次读取的结果。备注（`note`）永远不在这里。顶层可选的 `columns`（`[{id, zh, en, topics?, platforms?, types?}]`，最多 12 项）是目录主人定的栏目。这些字段对读目录的程序都是可选的：没有就当 `fixed` / `unknown` / 不指定 / 用自己的默认栏目。

`submit_api` 非空时，网页的「收录」直接把申请发给它（`POST`，JSON：`{"kind":"url","url":"…","rights":true}` 或 `{"kind":"post","title":"…","author":"…","tags":[…],"body":"Markdown","rights":true}`，书籍是 `{"kind":"record","title":"…","author":"…","tags":[…],"summary":"…","url":"（可选）","pages":242,"toc":["（可选）目录标题"],"rights":true}`，另有一个必须留空的隐藏字段 `website`）。成功返回 `{"ok":true,"status":"queued"}`；失败返回 `{"ok":false,"error":"…","message":"中文","message_en":"English"}`，`error` 是 `url` `post` `rights` `listed` `pending` `rejected` `rate` `full` `origin` `size` `github` `not_configured` 之一。

## 5. 托管投稿

读者可以把一篇 Markdown 文章交给目录托管（没有自己的网站、也不想用 Gist 的人）。通过审核后，文章存为仓库里的 `posts/<编号>.md`，构建成静态页面 `p/<编号>/`，并作为书架 `posts`（名称默认“社区投稿”）出现在 `shelves` 和 `items` 里（条目有 `hash` 字段，用来识别同一篇文字）。限制：标题 ≤120 字，作者名 ≤40 字，正文 60 到 50000 个字符，标签 ≤5 个；图片不显示，只保留链接；页面不含脚本、`noindex`。作者保留版权，可以随时要求下架或修改。

## 6. 下架

任何人都可以要求移除自己的内容：在仓库里提一个「下架」issue，或删掉原来的订阅源 / 页面（连续读不到会自动隐藏）。维护者也可以用 `python aggregate.py remove 编号`，或把网站写进 `registry/blocklist.txt`。
