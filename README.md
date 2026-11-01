# Pickcat 社区本人内容管理工具

非官方的 Pickcat(编程猫社区) API 客户端 + 命令行, 用于**管理你本人账号**下的内容:
定时发布自己的作品、列出/导出自己的帖子、查询投稿审核状态。

> ⚠️ 读这一段再决定要不要用。
> 这些是社区逆向出来的**非官方接口**, 官方未开放文档, 随时可能随服务端更新失效。
> 用脚本自动化操作社区, 大概率违反平台服务条款。本工具只面向"本人账号自我管理"
> 这一种合规场景, 并已内置护栏。**请不要**用来刷量、批量点赞、骚扰他人或操作非本人账号。

## 文件
- `pickcat_client.py` — 零依赖 HTTP 客户端 (urllib + cookiejar)。
- `pickcat_cli.py` — 命令行外壳。
- `.env.example` — 配置样例, 复制为 `.env` 填账号。

## 快速开始
```bash
cp .env.example .env      # 填入你自己的账号密码
python pickcat_cli.py ping          # 连通性自检 (不登录)
python pickcat_cli.py whoami        # 登录并打印账号信息
python pickcat_cli.py list --export md   # 列出并导出我的帖子
python pickcat_cli.py post --title "标题" --file article.md --dry-run   # 预览
python pickcat_cli.py post --title "标题" --body "正文" --at "2026-11-01 20:00"  # 定时发
python pickcat_cli.py status <submissionId>   # 查审核
```

## 登录有人机验证怎么办 (Cookie 复用)

密码直登如果弹出人机验证, 说明平台在登录接口加了反滥用闸门。**本工具不内置验证码求解器**——
那属于专门绕过平台防护、且基本要接付费打码的东西, 不在范围内, 也不可靠。

干净的合规做法是 **Cookie 复用**: 你在浏览器里以真人身份过掉验证、完成登录, 然后把浏览器
拿到的会话 Cookie 直接交给脚本。脚本不再走"密码登录"那条会被拦的路, 而是带着已建立的会话做事。

步骤:
1. 浏览器打开 pickcat 社区并登录 (人机验证由你本人过)。
2. F12 → Network → 随便点一条已登录后的请求 → Request Headers → 找到 `cookie:` 整串复制。
3. 二选一填入配置:
   - 环境变量/`.env`: `PICKCAT_COOKIE=session=xxxx; sid=yyyy; ...` (整串)
   - 或导出 Netscape 格式 `cookies.txt` (浏览器插件如 "Cookie-Editor" 可导出), 设 `PICKCAT_COOKIE_FILE=cookies.txt`
4. 此时 `.env` 里的 `PICKCAT_USERNAME` / `PICKCAT_PASSWORD` 可以留空。
5. 跑命令不用 `--user/--pass`: `python pickcat_cli.py whoami` 应直接返回你的账号信息。

注意:
- Cookie 会过期 (几小时到几天不等)。失效后 `whoami` 会报 401, 重新拷一次即可。
- Cookie 等同账号凭证, 别提交到公开仓库, 也别给别人。

## 已验证 vs 仅推断 (工程诚实, 必须分清)

| 项 | 状态 | 依据 |
|---|---|---|
| 主机 `pickcat.dao3.fun` 存活 | ✅ 实测 | 无鉴权 GET `/api/v1/session` 返回 401 `UNAUTHENTICATED` |
| `GET /api/v1/session` 取本人 | ✅ 实测 | 带 Cookie 返回 user/createdAt/expiresAt/silence |
| `POST /api/v1/session` 登录(邮箱+密码) | ⚠️ 仅源码 | 来自 Rust 逆向库, 但登录有人机验证, 实际走 Cookie 复用 |
| `GET /api/v1/users/{uid}/topics` 列本人帖子 | ✅ 实测 | 返回 items/pageInfo, 已用真实 uid 跑通 |
| `POST /api/v1/posts` 发帖(带 Idempotency-Key) | ✅ 实测成功 | 真实发帖已 PUBLISHED, submissionId 可查审核; 注意 host 必须用 `pickcat.dao3.fun`(见下) |
| `create_topic` 字段契约(kind/tagIds) | ✅ 实测 | camelCase 字段, `tagIds` 至少 1 个 (TOO_FEW, min:1), 已用真实标签 UUID 发帖成功 |
| `GET /api/v1/tags` 标签列表 | ✅ 实测 | 公开接口, 返回 11 个标签及其 UUID |
| `GET /api/v1/post-submissions/{id}` 查审核 | ✅ 实测 | 成功返回 PUBLISHED |
| **删除/编辑本人帖子** | ❌ 未实现 | 已确认的 API 表面里**不存在**该端点; 要删请去网页点 |

## ⚠️ 发帖 404 已解决: 根因是 302 重定向不是路径错

早期 `post` 稳定报 `404 ROUTE_NOT_FOUND`, 排查结论如下:

- `cdsq.dao3.fun` 会把**所有请求 302 重定向**到 `pickcat.dao3.fun`。
- Python `urllib` 遇到 302 时, 会把 `POST` **静默降级成 GET** 并丢弃 body。
  于是 `POST /api/v1/posts` 变成了 `GET /api/v1/posts` → 路由不存在 → 404。
- 这不是路径或字段错: `POST /api/v1/posts` 在 `pickcat.dao3.fun` 上**真实存在且可用**。

**修复(已写进代码):** 默认 `base` 直接用 `https://pickcat.dao3.fun` 直连,
不要走 `cdsq` 那个会重定向的域名。改完后发帖端到端跑通。

> 如果你以后换库/换语言, 记住这条: 凡是遇 302 会丢 body 的客户端(urllib 默认、部分 fetch 配置),
> 要么直连最终域名, 要么关掉自动跟随重定向并手动重写请求。

## 标签必填说明

发帖时 `tagIds` **至少 1 个**, 否则服务端返回 `TOO_FEW, min:1` 拒绝。
- 不想手查 UUID: 直接 `python pickcat_cli.py tags` 看全部标签;
- 或发帖时不传 `--tags`, 工具会自动取第一个可用标签(已在 dry-run 和真实发送都验证)。

## 半自动运营机器人 (`pickcat_bot.py`)

给"嘴替"场景用: 机器负责盯、判、护栏, LLM 负责写句子。

```bash
python pickcat_bot.py check                      # 扫描: 谁回了我 + AI 帖候选 + 数据异常
python pickcat_bot.py reply  --topic-id <id> --reply-to <楼层> --body "..."
python pickcat_bot.py comment --topic-id <id> --body "..."   # 评一条顶层评论
python pickcat_bot.py watch  --topic-id <id>                # 加入关注列表
python pickcat_bot.py state                                  # 看状态
```

硬护栏 (代码强制, 不靠自觉):
- **每帖顶层评论 <= 3 条** (只数我自己发的), 楼中楼回复不限
- 每日评论 <= 4 / 每日回复 <= 8 (可用环境变量调)
- 写操作最小间隔 8 秒
- 只动自己账号

状态存在 `.bot_state.json` (已 gitignore): 关注列表 + 每帖 `last_seen` + 每日计数。
凭证从 `.env` 的 `PICKCAT_COOKIE` 读, 输出里绝不回显。

`check` 的输出分三块:
- `[待回复]` — 有人回了我参与过的帖子 (优先处理)
- `[AI 帖候选]` — feed 里适合评论的 AI 话题 (脚本会标出"我的顶层评论数", >=3 会被拦)
- `[⚠ 数据异常]` — 该帖回复列表被服务端截断, **"没人回"可能是假的**, 别当无事发生

## 隐形水印 (标记"这条是 API 发的")

每条**经 API 发出的内容**, 正文末尾会自动追加 7 个不可见字符, 组成一个定长水印组:

```
[6 位 base-6 序号][1 位校验和]
```

字符表 (按需求含 U+200D, 但它是备胎, 见下):
`U+2060` WORD JOINER / `U+2061` FUNCTION APPLICATION / `U+2062` INVISIBLE TIMES /
`U+2063` INVISIBLE SEPARATOR / `U+2064` INVISIBLE PLUS / `U+200D` ZWJ

两个用途:
1. **溯源** —— 水印是**可验证**的标记: 任何人都能扒开原始文本看到, 从而知道这条是机器人发的。
   比"感觉像 AI"这种主观判断可靠, 也比每条都自曝身份自然。
2. **计数** —— 序号单调递增, 于是能统计调用量, 还能看出**哪几次调用没落地**
   (序号缺口 = 被拒 / 审核未过 / 被删)。

实测结论 (别再猜):
- 服务端**存储的原文**保留这些字符 ✅
- **公开渲染的 cookedHtml** 也保留 ✅ (所以别人真能扒到, 溯源目的达成)
- 编码 roundtrip 自测 60+ 序号全通过, 普通中文文本零误报
- `wm_strip()` 能精确还原原文

选型说明:
- 主体用 U+2060~U+2064: 它们语义上就是"不可见", 零宽, 不参与排版。
- **U+200D 有坑**: ZWJ 在 emoji 序列里有实义 (👨‍👩‍👧‍👦), 乱插可能影响渲染;
  它只排在组内且不落在 emoji 中间, 但仍是备选而非首选。
- 不选 U+00AD (软连字符, 换行处会显形) / U+FEFF (BOM, 语义特殊)。

```bash
python pickcat_bot.py audit    # 审计: 带水印/无水印条数、序号列表、缺口
python pickcat_bot.py verify   # 验证水印在服务端是否还活着
```

关闭水印: `create_topic(..., watermark=False)` / `create_reply(..., watermark=False)`。

注意:
- 别人复制粘贴你的话会把水印一起带走 (所以 `audit` 按作者过滤, 只统计自己发的)。
- 6 位 base-6 最多编码 46655 次调用, 超了要换更宽的编码。
- 水印上线**之前**发的内容没有水印, `audit` 里会归到"无水印", 按日期区分。

## 已知 API 坑 (实测, 别重复踩)

| 坑 | 表现 | 对策 |
|---|---|---|
| **302 重定向** | `cdsq.dao3.fun` 把 POST 302 到 `pickcat.dao3.fun`, urllib 遇 302 把 POST 降级成 GET 并丢 body -> 发帖必 404 | base 直连 `pickcat.dao3.fun` |
| **回复列表被截断** | `GET /topics/{id}/posts` 会少给回复; 帖级 `repliesTruncated` 常驻 true, `replyCount` 也可能偏小 | 靠 `replyCount` 增长但列表无新楼层来发现, 出现就人工看一眼网页版 |
| **单帖查询才是准的** | `GET /posts/{postId}` 能拿到确切状态 (`replyToPostNumber` / `publishedAt` / `latestSubmissionStatus`) | 排查单条回复时用它 |
| **`children` 恒为空** | 楼中楼不挂在 `children` 里, 而是平铺进 `items` (用 `replyToPostNumber` 区分) | 遍历时按 `replyToPostNumber` 判断层级 |
| **参数很挑** | `sort` 只认 `latest`; `limit` 上限约 100 (给 200 直接 400); `GET /post-submissions` 列表接口一律 400 (只有单条 `/{id}` 能用) | 见下方"接口清单" |
| **发帖要 tag** | `tagIds` 至少 1 个, 空了报 `TOO_FEW, min:1` | `python pickcat_cli.py tags` 查真实 UUID |

## 接口清单 (实测可用)

| 端点 | 鉴权 | 说明 |
|---|---|---|
| `GET /api/v1/session` | 需登录 | 取本人 (user / expiresAt / silence) |
| `POST /api/v1/session` | - | 邮箱+密码登录 (有人机验证, 实测走不通) |
| `GET /api/v1/users/{uid}/topics` | 需登录 | 列本人主题 |
| `GET /api/v1/topics` | 公开 | **社区帖子 feed**, 支持 limit + 游标分页 |
| `GET /api/v1/topics/{id}` | 公开 | 主题详情 (含 replyCount / repliesTruncated / lastActivityAt) |
| `GET /api/v1/topics/{id}/posts` | 公开 | 主题下的回复 (可能被截断) |
| `GET /api/v1/posts/{postId}` | 公开 | 单条回复详情 (准确) |
| `GET /api/v1/tags` | 公开 | 标签列表 (发帖必用) |
| `POST /api/v1/posts` | 需登录 | 发帖/回帖 (需 Idempotency-Key, 返回 202 异步审核) |
| `GET /api/v1/post-submissions/{id}` | 需登录 | 查审核状态 (PENDING_PROVIDER -> PENDING_PUBLICATION -> PUBLISHED) |

## 内置护栏 (为什么这样设计)
1. **写操作强制间隔** (`--min-interval`, 默认 5s): 防止脚本瞬间狂发被风控。
2. **`--dry-run`**: 写操作只打印"将发送什么", 不真正发请求。建议第一次先用它确认。
3. **仅限本人 uid**: `list_user_topics` 等会比对登录账号 uid, 越权直接拦下。
4. **会话 Cookie 模式**: 登录后凭据由 cookiejar 保存, 不落盘、不打印密码。

## 定时发布说明
`post --at "YYYY-MM-DD HH:MM"` 是**进程内 sleep 到点发布**, 适合临时用。
若要稳定定时 (关掉终端也发), 请用系统调度:
- Windows: 任务计划程序 或 `schtasks` 调本命令
- Linux/macOS: `cron` 调本命令
(把 `--at` 换成直接 `--body` 即可, 由调度器控制时间。)

## 如何自己核实接口 (强烈建议)
1. 浏览器打开 pickcat 社区并登录。
2. F12 → Network, 发一条帖子, 看真正的请求:
   - 请求的完整 URL (确认 base 是 `pickcat.dao3.fun/api/v1`, **不是**会 302 的 `cdsq`)
   - 是否有 `Idempotency-Key` 头、值格式
   - `kind` / `tagIds`(注意是 camelCase) 的真实取值
   - 有没有 DELETE/PATCH 主题的请求 (用来补全"删帖"能力)
3. 把核对结果回填到 `pickcat_client.py` 对应方法即可。

## 免责
本工具仅供你管理自己账号内容使用。因使用本工具导致的封号、内容下架等后果由使用者自行承担。

## 许可证
[MIT](LICENSE)。可自由学习、修改、再分发, 但请保留原作者声明, 并**不要**用它做违反平台规则或伤害社区的事。
