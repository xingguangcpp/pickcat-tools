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
- 每日评论 <= 3 / 每日回复 <= 12 (在 `.env` 里调, 改完真生效 —— 见下方"参数从哪读")
- 写操作最小间隔 8 秒
- 只动自己账号

状态存在 `.bot_state.json` (已 gitignore): 关注列表 + 每帖 `last_seen` + 每日计数。
凭证从 `.env` 的 `PICKCAT_COOKIE` 读, 输出里绝不回显。

`check` 的输出分三块:
- `[待回复]` — 有人回了我参与过的帖子 (优先处理)
- `[AI 帖候选]` — feed 里适合评论的 AI 话题 (脚本会标出"我的顶层评论数", >=3 会被拦)
- `[⚠ 数据异常]` — 该帖回复列表被服务端截断, **"没人回"可能是假的**, 别当无事发生

## 隐形水印 (标记"这条是 API 发的")

每条**经 API 发出的内容**都会被打上标记, 分两层:

| 层 | 位置 | 长什么样 | 用途 |
|---|---|---|---|
| **隐形水印组** | 标题末尾 + 正文末尾 | 7 个零宽字符 | 机器可读: 解出序号, 用于计数/溯源 |
| **选中可见标记** | 正文末尾 | 2 个 `U+200B` | 人类可察觉: 排版上看不见, **选中时显示成一小段空白** |

隐形水印组 = **6 位 base-6 序号 + 1 位校验和**(`sum(digits) % 6`), 定长 7 字符。
标题与正文共用同一个序号 —— 一次写操作 = 一个 seq。

字符表:
`U+2060` WORD JOINER / `U+2061` FUNCTION APPLICATION / `U+2062` INVISIBLE TIMES /
`U+2063` INVISIBLE SEPARATOR / `U+2064` INVISIBLE PLUS / `U+200D` ZWJ

两个用途:
1. **溯源** —— 水印是**可验证**的标记: 任何人都能扒开原始文本看到, 从而知道这条是机器人发的。
   比"感觉像 AI"这种主观判断可靠, 也比每条都自曝身份自然。而 `U+200B` 那层更是
   连源码都不用扒 —— 选中正文就能看见异常的空隙。
2. **计数** —— 序号单调递增, 于是能统计调用量, 还能看出**哪几次调用没落地**。

实测结论 (别再猜):
- 服务端**存储的原文**保留这些字符 ✅
- **公开渲染的 cookedHtml** 也保留 ✅ (所以别人真能扒到, 溯源目的达成)
- `U+200B` 可见标记也活过服务端清洗 ✅ (2 个, 数量精确对得上)
- 编码 roundtrip 自测 60+ 序号全通过, 普通中文文本零误报
- `wm_strip()` 能精确还原原文 (含去掉可见标记)

### ⚠️ 踩过的坑: 别把不可见字符直接写进源码

第一版我把 `WM_VISIBLE = "\u200b"` 直接写成**真实的 U+200B 字符**存进 .py 文件,
结果那个字符在写入时被吞掉(变成空字符串), 功能静默失效 —— 而且因为
`"abc".count("")` 返回 `len+1`, 我一度以为"有 959 个标记"。

**必须用 `"\u200b"` 这样的转义序列写进源码。** 现在字母表和可见标记全部是转义写法,
源文件这几行是纯 ASCII, 不怕编辑器/复制粘贴/格式化工具损坏。

另一个同类坑: U+200D 在 emoji 序列里有实义 (👨‍👩‍👧‍👦), 乱插会搞坏表情;
U+00AD (软连字符) 换行处会显形; U+FEFF (BOM) 语义太特殊。都不如 U+2060~U+2064 干净。

### 调用量以本地账本为准 (别靠服务端列表数)

服务端列表接口靠不住: 会截断 (`repliesTruncated`)、也看不到主题首楼 —— 靠它统计必然漏。
所以每次写操作都会往本地账本 `.wm_ledger.json` 落一条记录 (seq / 类型 / postId / 状态 / 时间),
**账本才是调用量的权威来源**。

```bash
python pickcat_bot.py audit          # 调用量 + 服务端水印抽查 + 序号缺口
python pickcat_bot.py audit --all    # 全量抽查
python pickcat_bot.py verify         # 验证水印在服务端是否还活着
```

`audit` 会做三件事: ① 账本统计(总调用量 / 发帖 vs 回帖 / 序号缺口) ② 抽查账本里的
postId, 确认服务端存的原文里水印还在、序号对得上 ③ 反向扫描列表, 找出"带水印但不在账本"
的内容(比如用别的工具发的)。

关闭水印: `create_topic(..., watermark=False)` / `create_reply(..., watermark=False)`。

注意:
- 别人复制粘贴你的话会把水印一起带走 (所以 `audit` 按作者过滤, 只统计自己发的)。
- 6 位 base-6 最多编码 46655 次调用, 超了要换更宽的编码。
- 水印上线**之前**发的内容没有水印, `audit` 里会归到"无水印", 按日期区分。

## 服务不可用时怎么表现 (宕机检测)

社区 API 后端挂掉时的实测特征 (**2026-11-01 19:00 后遇到过一次**):

| 现象 | 说明 |
|---|---|
| 网页 `/` | **200**, Nuxt 壳正常渲染 —— 但只有标题, 没有数据 |
| `/api/v1/*` | **502 Bad Gateway**, 每个请求卡满 10 秒才返回 |
| 报错体 | `{"error":true,"url":"http://127.0.0.1/api/v1/...","statusCode":502}` —— 说明是 Node 网关转发上游 API 超时 |
| 其它域名 | `cdsq` 301, `staging` 503, `api/dev/test` 证书不匹配 —— 没有备用 API 入口 |

所以**"网页能开"不等于服务正常** —— 前端是壳，数据全靠 `/api/v1`。用户看到的是白页或报错。

工具的处理:
- 一次 502 就判定不可用 (`OUTAGE_THRESHOLD=1`), 落盘 `.wm_outage.json`
- 判定后进入 **10 分钟冷却**: 期间所有命令**瞬时返回**, 不发任何请求 (避免每小时空转一轮)
- 命令返回的是一句人话 ("⛔ 服务不可用 ... 这是服务端问题, 与凭证/配额无关"), 而不是 traceback
- 冷却期过会自动再探一次, 恢复了就自动继续

```bash
python pickcat_bot.py check     # 宕机时秒退, 不空转
```

## 每日配额 (最容易踩的一个坑: 它是**异步失败**的)

平台对写操作有**每日额度**, 实测:

| 动作 | 额度 | 窗口 |
|---|---|---|
| `TOPIC_CREATE` (发帖) | **10 个 / 天** | 24 小时滚动 (`windowSeconds: 86400`) |
| `TOPIC_REPLY` (评论/回帖) | 独立计算, 与发帖分开 | 同上 |

**坑在"异步"**: `POST /api/v1/posts` 会正常返回 `202` + `submissionId`, 看起来一切正常。
真正失败发生在**审核阶段**, 得回查 `GET /api/v1/post-submissions/{id}` 才看得到:

```
status        = PUBLICATION_FAILED
reasonCode    = QUOTA_EXCEEDED
reasonDetails = { "action": "TOPIC_CREATE", "used": 10, "limit": 10,
                  "windowSeconds": 86400, "availableAt": "2026-11-01T05:42:38.015Z" }
```

所以 **光看 POST 的返回值判断不了成功**, 必须回查审核状态。
`reasonDetails` 这套结构还挺好用 —— 动作类型、用量、上限、窗口长度、恢复时间全给了。

工具的处理:
- `pickcat_bot.py refresh` —— 复查所有未完成投稿的审核状态, 并把配额信息落盘
  (`.wm_quota.json`)。**每次写操作后都应该跑一次**, 否则会一次次白跑。
- **配额闸门** —— `check_quota(action)` 在写操作前检查: 额度用尽时**主动拦下**,
  不浪费调用去换一次失败。闸门**按动作分别生效**: 发帖额度用尽时回帖照常,
  嘴替不会哑火。
- 每日上限: 评论 3 / 回复 12 (按自然日 0 点清零)。评论是"评新帖"的刷量方向, 卡得紧;
  回复是"回具体楼层"的嘴替本职, 卡太死会让人干等着 —— 平台真正的限制在发帖额度上。

```bash
python pickcat_bot.py refresh   # 复查审核状态 + 更新配额
python pickcat_bot.py quota     # 看当前配额与恢复时间
```

## 多账号: 把 AI 内容和你自己写的内容分开

一个号一份 cookie、一份账本、一份配额、一份序号 —— 用来把"AI 代发"和"人写的"彻底隔开。
典型用法: 主号(default)你自己写, AI 号(aibot)专门跑自动化。

```bash
python pickcat_bot.py --account aibot check
python pickcat_cli.py  --account aibot post --title "标题" --body "正文"
# 或设环境变量 PICKCAT_PROFILE=aibot
```

| 档案 | 凭证 | 状态文件 |
|---|---|---|
| `default` | 工具根目录 `.env` | 根目录 `.wm_ledger.json` / `.wm_quota.json` / `.wm_counter.json` / `.bot_state.json` |
| 其他 (`aibot` 等) | `accounts/<名字>.env` | `.state/<名字>.wm_ledger.json` / `.state/<名字>.wm_quota.json` / ... |

所以两个号的**水印序号各自从 1 开始**, 账本互不混淆, 审计时能一眼看出某条内容出自哪个号。
`accounts/*.env` 和 `.state/` 都已 gitignore。详见 [`accounts/README.md`](accounts/README.md)。

新号得你本人在浏览器注册(平台没开放注册接口, 登录还有人机验证), 流程见 accounts/README.md。
注意: **新号不等于无限额度** —— 发帖额度是按账号算的(实测 `TOPIC_CREATE` 10 个/24h), 新号只是重新从 0 开始。

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
