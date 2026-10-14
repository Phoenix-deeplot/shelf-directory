# 公共目录（shelf-directory）

「个人数字图书馆」的公共目录：把很多人的博客、文章、GitHub 仓库、视频订阅源聚合成一份目录。
**不收文章、不设账号、不存正文**，只记标题、摘要、封面图地址和回到原站的链接。数据放在这个独立的仓库里，由 GitHub Pages 发布，网页的「公共目录」直接读它。

```
访客贴一个网址 ──▶ GitHub issue（收录申请表单）
                      │  机器人自动识别、检查，回复结果
                      ▼
          你（维护者）看一眼，给 issue 加标签 approved
                      │
                      ▼
   aggregate.py 登记 + 读取 ──▶ registry/（登记表、快照）
                      │                 每天 03:17 UTC 自动重读
                      ▼
          docs/（directory.json、订阅源、OPML） ──▶ GitHub Pages ──▶ 网页「公共目录」
```

规范见 [SHELF-SPEC.md](SHELF-SPEC.md)。

## 一次性设置（约 10 分钟）

1. 在 GitHub 新建一个**公开**仓库（例如 `shelf-directory`），把这个文件夹推上去。
2. 仓库 **Settings → Pages**：Source 选 *Deploy from a branch*，分支 `main`，文件夹 `/docs`。
3. 仓库 **Settings → Actions → General → Workflow permissions**：选 *Read and write permissions*。
4. 打开 **Actions → Daily → Run workflow** 手动跑一次（它也会创建需要的标签）。
5. 等一两分钟，打开 `https://你的用户名.github.io/仓库名/directory.json`，能看到数据就成功了。
6. 在 `my-archive` 项目的 `site.json` 里加一行：`"directory": "https://你的用户名.github.io/仓库名/"`，页面左侧就会出现「我的书架 | 公共目录」。

`registry/config.json` 可改目录的标题；`base_url` 留空时 Action 会自己推断（`https://用户名.github.io/仓库名/`）。如果用自己的域名，把它写在 `base_url` 里。

## 日常

- **收到申请**：issue 里机器人已经回复了识别结果（名称、找到几篇、最新几条、语言、标签、警告）。没问题就给 issue 加上标签 `approved`，几十秒后它会被登记、读取、写入 `docs/`，issue 自动关闭。不合适就直接关掉 issue，或写一句理由。
- **每天自动**：重读所有来源，只有内容变了才提交。某个来源连续 7 天读不到会暂时从目录隐藏，恢复后自动回来。
- **下架**：收到「下架」issue，或你想移除某个书架：`python aggregate.py remove 编号`，再 `git push`。想永久拒绝某个站点，把它写进 `registry/blocklist.txt`。
- **标签**：收录时申请人可以填，也会从来源自动推断。近义词在 `registry/tags.json` 里归并。

## 本机命令

需要 Python 3.9+，没有第三方依赖。

```bash
python aggregate.py probe https://example.com/blog      # 看看一个网址会被识别成什么（什么都不写）
python aggregate.py approve https://example.com/blog --tags 供应链,笔记   # 直接收录
python aggregate.py fetch            # 读取所有来源
python aggregate.py build            # 生成 docs/
python aggregate.py run              # fetch + build
python aggregate.py serve            # 在 http://127.0.0.1:8770/ 预览 docs/（带 CORS，给本机的网页用）
python aggregate.py remove 编号
python -X utf8 -m unittest discover -s tests -v          # 测试（不联网）
```

本机试用整个流程：`python aggregate.py serve`，再把 `my-archive` 的 `site.json` 写成 `"directory": "http://127.0.0.1:8770/"`，运行 `python serve.py`。

## 它怎么保证安全

- 只走 https:443，域名必须解析到公网地址，重定向每一跳都重新检查；响应 ≤2 MB；每次请求 15 秒超时。
- XML 里有 DOCTYPE / ENTITY 的订阅源直接拒绝。网页只在 `robots.txt` 允许时读取。
- issue 里的文字只当数据读：由 Python 从事件文件里解析，从不拼进 shell 命令；机器人回复里的标题、链接都做了转义（不会 @ 到别人）。
- 目录页面不含任何统计脚本；收录的链接在网页里带 `rel="nofollow ugc noopener"`。
- 许可：这个仓库没有附带许可证，你自己决定（建议 MIT 或 CC0）。
