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
   你在管理页 /admin/ 点「通过」或「拒绝」（管理页直接用你的 GitHub 令牌操作 issue，没有后台）
        │  通过 = 给 issue 加标签 approved
        ▼
   Action 登记（订阅源 / 仓库 / 单条）或发布文章页 ──▶ registry/、posts/ ──▶ 每天 03:17 UTC 重读
        ▼
   docs/（directory.json、订阅源、OPML、文章页 p/）──▶ GitHub Pages ──▶ 网页「公共目录」
```

规范见 [SHELF-SPEC.md](SHELF-SPEC.md)。

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
   | `IP_SALT` | 一长串随机文字（用来给发送者地址做不可逆的短哈希，用于限流） |

3. 改完环境变量要再 `vercel --prod` 一次。把 Vercel 给你的地址加上 `/api/submit`，写进 `registry/config.json` 的 `"submit_api"`，推送。下一次 Action 生成的 `directory.json` 里就有它，网页的「收录」对话框会自动换成"直接提交 + 写文章"两个标签。
4. 用浏览器打开 `https://…/api/submit`，应该看到 `{"ok": true, "service": "shelf-submit", "configured": true, …}`。

   **想要一次 `git push` 就全部更新**：在 Vercel 里把这个项目连接到 GitHub 仓库（项目 Settings → Git → Connect），以后推送就会自动部署函数和管理页，不用再手动 `vercel --prod`。（环境变量改了仍要重新部署一次。）

函数有两个动作，都不需要访客有 GitHub 账号：

- **检查**（`kind: "check"`，什么都不创建）：先对照目录和审核队列排重（已收录、正在审核、已被拒绝都直接告诉访客），再**去读这个网址**，返回它是什么、找到几篇、最近几条、语言、许可、警告，或者读不了的原因。读取用的是和 Action 同一套受限代码（只走 https:443、只连公网地址并固定到解析出的那个 IP、重定向每一跳重查、大小和时间有限、网页要看 robots.txt），每个发送者 10 分钟最多 20 次。文章只做本地检查（长度、链接、屏蔽词、内容哈希），不联网。
- **提交**（`kind: "url" | "post"`）：同样的排重，再写成 issue。不读这个网址，读取留给 Action。

排重先读**仓库里的** `registry/shelves.json`（刚通过的马上算），再读已发布的 `directory.json`；不用数据库：队列就是仓库里待审的 issue，限流靠 issue 里的一个加盐哈希。

### C. 管理页

打开 `https://你的用户名.github.io/仓库名/admin/`。第一次要贴一个 GitHub 令牌（页面上有取法）：fine-grained，只选这个仓库，**Issues: Read and write** 和 **Actions: Read and write**。令牌只存在你的浏览器里（默认关闭标签页就忘掉，可以勾选“记住”），页面直接调 GitHub 的接口，没有任何后台。

- **待审**：每个申请显示识别结果（机器人的回复）、检查状态（通过 / 未通过 / 重复）；文章投稿可以看全文预览。「通过」「拒绝…」（可写理由，只记在 issue 里，不会告诉申请人）。通过后等 Action 登记，页面会告诉你结果。
- **已收录**：所有书架，每个有「删除」；托管文章单独列出，每篇有「删除」。「立即更新目录」会马上重读所有来源。
- **已处理**：最近通过和拒绝的申请。

## 日常

- **收到申请**：打开管理页处理；也可以仍然在 GitHub 的 issue 里给申请加标签 `approved` / `rejected`，效果一样。
- **每天自动**：重读所有来源，只有内容变了才提交。某个来源连续 7 天读不到会暂时从目录隐藏，恢复后自动回来。
- **下架**：管理页「删除」；或 `python aggregate.py remove 编号`（托管文章用 `post:编号`）后推送。想永久拒绝某个站点，写进 `registry/blocklist.txt`；想拦掉某些词，写进 `registry/blockwords.txt`（每行一个，文章里出现就拒绝）。
- **标签**：收录时申请人可以填，也会从来源自动推断。近义词在 `registry/tags.json` 里归并。

## 重复是怎么过滤的

1. **网址归一**：`http` / `https`、`www`、末尾斜杠、`index.html`、`utm_*` 等跟踪参数、参数顺序、`youtu.be/ID` 与 `youtube.com/watch?v=ID`、`github.com/o/r.git`，都算同一个。
2. **提交前后查三次**：网页在你敲网址时就用已加载的目录比对（已有就直接说，不让提交）；函数在“检查”和“提交”时各查一次（读仓库里最新的登记表 + 已发布的目录 + 待审队列）；Action 在 issue 打开时和通过时再查一次。查的内容：已在目录里（同一来源、同一篇、仓库或它的上层文件夹）、已在审核中、已被拒绝（只记哈希，不告诉理由）、同一篇文章（忽略空白和 Markdown 标记）。只有走 GitHub 申请表（绕过了网页和函数）的重复申请会先变成 issue，由 Action 贴上“重复”标签，管理页里“通过”按钮不可点。
3. **GitHub 仓库**：fork 直接拒绝并指出原仓库；同一个文件内容（git 的内容哈希）在一个或多个仓库里只留最早登记的那份。
4. **订阅源条目**：同一个链接出现在多个来源里只留一条。站点主人可以在订阅源里用 `_shelf.listed = false` 让某一条不进任何目录（本站的 `directory: false`）。

## 本机命令

需要 Python 3.9+，没有第三方依赖。

```bash
python aggregate.py probe https://example.com/blog      # 看看一个网址会被识别成什么（什么都不写）
python aggregate.py approve https://example.com/blog --tags 供应链,笔记   # 直接收录
python aggregate.py fetch            # 读取所有来源
python aggregate.py build [--base http://127.0.0.1:8770/]   # 生成 docs/（托管文章的网址需要 base）
python aggregate.py run              # fetch + build
python aggregate.py serve            # 在 http://127.0.0.1:8770/ 预览 docs/（带 CORS，给本机的网页用）
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
