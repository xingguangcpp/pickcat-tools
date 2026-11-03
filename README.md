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
python pickcat_bot.py notify                     # 谁回了我 (读通知, 权威判据)
python pickcat_bot.py reconcile                  # 独立对账: 用第二套判据复核"还有没有人回我"
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

### `reconcile`: 拿第二套判据复核 (别只信 notify)

`notify` 和 `check` 都是**单侧视图**, 各自有盲区:

| 命令 | 判据 | 已知盲区 |
|---|---|---|
| `notify` | 通知 + `notified_handled` 标记 + 7 天时间窗 | 标记漏写会永久漏掉; 超窗的老回信不再提示 |
| `check` | 扫帖 + `last_seen` 水位 | 水位推进过猛会把低层未回的吞掉 |

历史上多次出现"报告 0 条待回复, 实际有人回了没被看见"(11-03 00:11 那轮翻页才捞出 2 条)。

`reconcile` **绕开那两个状态**, 只用一个客观判据: **那个楼层我是否已经接过话**
(服务端我的楼层 `replyToPostNumber` + 本地账本 `replyTo`)。两路数据源取并集:
- **通知侧**: 全量翻页拉 `REPLY_CREATED`, 逐条查是否已回过
- **帖子侧**: 扫我的全部主题 + 所有关注帖, 找"父楼层作者是我"的楼

```bash
python pickcat_bot.py reconcile              # 只报时间窗内的真·待回
python pickcat_bot.py reconcile --verbose    # 连超窗条目一起列(备查)
```

**只读, 不写任何状态。** 当 `notify` 报 0 条时, 跑一次 `reconcile` 交叉验证 ——
两边一致才能下"确实没人回我"的结论。它还会顺带统计**孤儿楼层**
(`replyToPostNumber > 1` 但父楼层已被删), 那种楼层没法判定归属, 是潜在假阴性来源。

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

## API 实时状态 (自动生成, 一直在更新)

手写的接口清单会过期, 所以接口表面是**巡检出来的**, 不是抄出来的:

| 文件 | 内容 | 谁维护 |
|---|---|---|
| [`docs/API-STATUS.md`](docs/API-STATUS.md) | 全部已知端点的**当前**状态表 (状态/耗时/条目数/错误码) | `pickcat_probe.py` 每次运行重新生成 |
| [`docs/API-CHANGELOG.md`](docs/API-CHANGELOG.md) | 变化流水: 端点新增/消失/状态变/结构变 | 有变化时自动追加 |
| [`docs/api-snapshot.json`](docs/api-snapshot.json) | 机器可读快照, 供 diff | 每次覆盖 |

```bash
python pickcat_probe.py            # 跑一轮巡检 (只读)
python pickcat_probe.py --list     # 只看探测清单
python pickcat_probe.py --json     # 额外输出机器可读结果
python pickcat_probe.py --always   # 强制心跳 (即使没变化也刷新时间戳并提交)
python test_probe_logic.py         # 巡检器自身的逻辑回归测试 (纯本地, 不联网)
```

- 巡检**只发 GET**, 绝不写、绝不消耗每日发帖额度
- 发现变化时**自动 commit + push** 到 GitHub (需 `.env` 里 `PICKCAT_PROBE_AUTOPUSH=1` + `GITHUB_TOKEN`)
- 配套的定时任务每 3 小时跑一轮, 所以文档一直是最新的
- 服务不可用时安静跳过, 不空转
- **心跳**: 即使没有语义变化, 每 12 小时也会提交一次"巡检时间戳刷新"
  (间隔用 `PICKCAT_PROBE_HEARTBEAT_HOURS` 调, 设 0 关闭)。否则状态表上的时间戳
  会一直停在最后一次有变化的那轮, 看起来像很久没巡检。

### 推送失败不会把变化吞掉

对比基线用的是 `.probe_published.json` (**上次成功推送**时的快照), 不是本地那份
`docs/api-snapshot.json`。因为后者每轮都被覆盖: 如果某轮 push 失败 (token 过期/
网络问题), 那一轮的变化已经写进本地文件了, 下一轮再拿它当基线就会"没变化"——
变化被静默吞掉, 永远不会再推。

改成拿"已发布基线"对比后: 推送失败 → 基线不动 → 下一轮**重新检测到**同一组变化
→ 重新提交推送。CHANGELOG 侧用变化指纹 (`.probe_changelog_key`) 去重, 重试不会
把同一段记录灌进日志。

这两个文件都是本机状态, 已 gitignore。

## 「谁回了我」: 用通知, 别用扫帖子

`GET /api/v1/notifications` 里有 `REPLY_CREATED` 类型, 直接给出**谁 / 哪个帖 / 几楼**:

```json
{"type":"REPLY_CREATED","createdAt":"...",
 "details":{"actor":{"id":"...","username":"林颢文"},
            "topic":{"id":"01a194c3...","title":"鲸鱼娘教你用 API 给 Pickcat 发帖～"},
            "post":{"id":"...","postNumber":9}}}
```

通知类型实测有 `POST_LIKED` / `REPLY_CREATED` / `USER_FOLLOWED` 三种。

这比"扫帖子 + 猜谁回我"可靠得多 —— 实测同一时间点:
扫帖子的方式是 **12 条里只有 5 条为真**(7 条误报), 而通知是 **12 条全部为真、零误报**,
而且能覆盖到你压根没参与过的帖子(比如别人在你回复过的第三方帖里接着回你)。

```bash
python pickcat_bot.py notify                    # 谁回了我(未处理的)
python pickcat_bot.py reply --topic-id ... --reply-to 9 --body "..." --notify <通知id>
#                                                                        └ 回完顺手标记已处理
```

## 社区等级门槛: level ≥ 1 才能写

实测**发帖和评论都要求社区等级 ≥ 1**。新注册账号是 level 0, 连评论都会被拒:

```
POST /api/v1/posts
-> 403 PARTICIPATION_LEVEL_REQUIRED
   {"currentLevel": 0, "requiredLevel": 1}
```

等级和统计是**可读**的 —— `GET /api/v1/users/{uid}` 会返回:

```json
"level": {"current": 0},
"stats": {"followers": 0, "following": 0, "topics": 0, "replies": 0}
```

所以工具能做**写前预检**: `check_participation()` 发现等级不够就直接抛错,
不浪费一次调用去换 403, 也不会白白消耗水印序号(闸门放在消耗序号**之前**):

```bash
python pickcat_cli.py --account aibot post --title "..." --body "..."
# -> 发布失败: 社区等级不足: 当前 0, 需要 1
#    hint: 新号需先完成社区的入驻流程(在网页版看看等级/任务提示)升到 level 1
```

**等级怎么升没有公开接口说明** —— 实测结果:
`/api/v1/levels` 是 404 (路由根本不存在), `/api/v1/permissions` 和 `/api/v1/users/{uid}/level`
登录后仍 403 (分别要 `access.manage` / `community.level.manage` 权限), 所以以网页版的提示为准。

> 社区里有人专门开帖讨论过这个门槛(「这入站考试好像变难了」、「！我通过入站考试了！（第3次）」),
> 从回复里能拼出大概: **level 1 需要通过"入站考试"** —— 题目是 **洛谷(Luogu) 的 P 开头题号**
> (如 P17829 [CSP-J 2026] 按位与 / and), **必须用 C++** 提交(社区里推荐 Dev-C++ / VSCode / nano),
> 有数据范围坑(要开 long long, 有人提到 10^8+1 那题),
> **而且每次考试有冷却**, 所以有人考了 3 次才过。
> 这道门槛拦的是"会不会写代码", 所以工具**不会**去代考 —— 那是社区自己设的质量闸门, 绕过去没意义。
> 新号过不了考试就只能读, 写操作会被等级闸门挡住。

顺带: `PATCH /api/v1/users/{uid}` 是改资料的入口, 但 body 里必须带 `userId`;
而 `PATCH /api/v1/users/me` 会把路径里的 "me" 当成 userId 塞进校验, 于是报 `Invalid UUID`。

## 已知 API 坑 (实测, 别重复踩)

| 坑 | 表现 | 对策 |
|---|---|---|
| **302 重定向** | `cdsq.dao3.fun` 把 POST 302 到 `pickcat.dao3.fun`, urllib 遇 302 把 POST 降级成 GET 并丢 body -> 发帖必 404 | base 直连 `pickcat.dao3.fun` |
| **回复列表被截断** | `GET /topics/{id}/posts` 会少给回复; 帖级 `repliesTruncated` 常驻 true, `replyCount` 也可能偏小 | 靠 `replyCount` 增长但列表无新楼层来发现, 出现就人工看一眼网页版 |
| **单帖查询才是准的** | `GET /posts/{postId}` 能拿到确切状态 (`replyToPostNumber` / `publishedAt` / `latestSubmissionStatus`) | 排查单条回复时用它 |
| **`children` 恒为空** | 楼中楼不挂在 `children` 里, 而是平铺进 `items` (用 `replyToPostNumber` 区分) | 遍历时按 `replyToPostNumber` 判断层级 |
| **参数很挑** | `sort` 只认 `hot`/`latest`/`earliest`; `limit` 上限**按端点各不同** (notifications 是 50, 给 100 直接 400); `GET /post-submissions` 列表接口必须带 `contentRole`+`status`, 不带就 400 | 见下方"接口清单"; 上限值由巡检器的 `?limit=100` 探测项实测锁定 |
| **发帖要 tag** | `tagIds` 至少 1 个, 空了报 `TOO_FEW, min:1` | `python pickcat_cli.py tags` 查真实 UUID |
| **写操作是异步审核的** | POST 返回 202, 楼层要过审才出现在服务端 —— 刚发完立刻查楼层, 查到的还是**发帖前**的水位 | 推进 `last_seen` 要取 `max(服务端最大楼层, 发帖前水位+1)`, 不能直接写 `max_post_number` (新帖会记成 0, 水位过滤失效, 之后每轮把旧楼层当新楼层刷屏) |

## 接口清单 (实测可用)

> 完整实时状态见 [`docs/API-STATUS.md`](docs/API-STATUS.md)（自动生成）。
> 下面这张表是人工整理的要点, 详细的参数与坑看那份自动文档。

| 端点 | 鉴权 | 说明 |
|---|---|---|
| `GET /api/v1/session` | 需登录 | 取本人 (user / expiresAt / silence) |
| `POST /api/v1/session` | - | 邮箱+密码登录 (有人机验证, 实测走不通) |
| `GET /api/v1/topics?limit=N` | 公开 | **社区帖子 feed**, 带游标分页 |
| `GET /api/v1/topics/{id}` | 公开 | 主题详情 (replyCount / repliesTruncated / lastActivityAt / closedAt / pinned) |
| `GET /api/v1/topics/{id}/posts` | 公开 | 主题下的回复 (可能被截断; `sort` ∈ `hot`/`latest`/`earliest`) |
| `GET /api/v1/posts/{postId}` | 需登录 | 单条回复详情 (准确; 查单条回复时用它) |
| `GET /api/v1/users/{uid}` | 公开 | 用户资料 (username / bio / region) |
| `GET /api/v1/users/{uid}/topics` | 公开 | 列某人的主题 |
| `GET /api/v1/users/{uid}/posts?role=reply` | 公开 | 列某人的回复 (`role` 只认 `reply`) |
| `GET /api/v1/users/{uid}/followers` / `following` | 公开 | 粉丝 / 关注列表 |
| `GET /api/v1/users/{uid}/badges` | 公开 | 该用户已获徽章 |
| `GET /api/v1/notifications` | 需登录 | 通知 (`REPLY_CREATED` / `POST_LIKED` / `USER_FOLLOWED`) |
| `GET /api/v1/works` | 公开 | 作品列表 |
| `GET /api/v1/works/{workId}` | 公开 | 作品详情 (coinCount / favoriteCount / commentCount) |
| `GET /api/v1/badges` | 公开 | 徽章列表 (8 个) |
| `GET /api/v1/tags` | 公开 | 标签列表 (11 个, 带 slug) |
| `GET /api/v1/emoji-collections` | 公开 | 表情包集列表 (9 个) |
| `GET /api/v1/emoji-collections/{id}` | 公开 | 表情包集详情 (coverUrl / position / version) |
| `GET /api/v1/emojis?collectionId=<uuid>` | 公开 | 某个集合里的表情 (必须带 collectionId) |
| `GET /api/v1/health` | 公开 | 健康检查 (status / service / timestamp) |
| `POST /api/v1/posts` | 需登录 | 发帖/回帖 (需 Idempotency-Key, 返回 202 异步审核) |
| `GET /api/v1/post-submissions/{id}` | 需登录 | 查审核状态 (PENDING_PROVIDER → PENDING_PUBLICATION → PUBLISHED) |
| `GET /api/v1/post-submissions?contentRole=&status=` | 需登录 | 投稿列表 (**必须带这两个参数**; `contentRole`∈TOPIC_FIRST_POST/TOPIC_REPLY/WORK_COMMENT, `status`∈PENDING/REJECTED) |
| `GET /api/v1/files` | 需登录 | 我的文件列表 (id / ownership / uploadedByUserId) |
| `GET /api/v1/files/{fileId}` | 公开 | **文件原图直接可读** (返回二进制, 匿名也能取) |
| `GET /api/v1/users/{uid}/works` | 公开 | 列某人的作品 |
| `GET /api/v1/works/{workId}/comments` | 公开 | 作品下的评论 (`sort` ∈ `hot`/`latest`/`earliest`) |
| `GET /api/v1/works/{workId}/comments/{楼号}` | 需登录 | 单条作品评论 (楼号是**数字**, 从 1 开始) |
| `GET /api/v1/badges/{badgeId}` | 公开 | 徽章详情 (id 是 **slug** 如 `badge_bugfinder`, 不是 UUID) |
| `GET /api/v1/permissions` | 需权限 | 权限表 (登录了也 403, 要 `access.manage`) |
| `GET /api/v1/users/{uid}/level` | 需权限 | 等级管理 (登录了也 403, 要 `community.level.manage`) |
| `GET /api/v1/emojis/{emojiId}` | 需权限 | 表情管理 (登录了也 403, 要 `community.emoji.manage`) |

> 鉴权列是**匿名实测**的结果 (不带 cookie 直接打), 不是照抄文档。
> 巡检器每轮都会**自动**做一遍匿名复测, 端点哪天从公开改成需登录会立刻报警。
>
> **更正**: 旧版这里写「`GET /api/v1/files` 路由存在但恒 401, 尚未开放」—— **这是错的**。
> 匿名访问确实 401, 但**带 cookie 是 200**, 能正常列出文件; 而且 `GET /api/v1/files/{id}`
> **匿名就能读** (直接返回图片二进制)。当时只试了匿名, 就下了"恒 401"的结论。
> 这正是"错标注比缺端点更危险"的典型: 缺端点会被巡检发现, 错标注不会。

**每条路由的 `limit` 上限各不相同**, 用超限值问出来:

| 端点 | limit 上限 |
|---|---|
| `/api/v1/topics` | 50 |
| `/api/v1/topics/{id}/posts` | 100 |
| `/api/v1/works` | 50 |
| `/api/v1/users/{uid}/posts` | 50 |
| `/api/v1/users/{uid}/works` | 50 |
| `/api/v1/notifications` | 50 |
| `/api/v1/files` | 100 |
| `/api/v1/works/{id}/comments` | 100 |
| `/api/v1/badges` | 不接受上限 (传 101 也 200, 但 `limit` 不生效) |
| `/api/v1/tags` / `emoji-collections` / `emojis` | 无 `limit` 参数 (传了直接 400) |

**`sort` 的枚举分两套, 别混**:

- 社区 feed `/api/v1/topics`: `relevance` / `latest`, 且 `relevance` 在浏览模式下会被拒
  (`{"sort":["浏览模式不能按相关性排序"]}`)。
- 主题内回复 `/api/v1/topics/{id}/posts`: `hot` / `latest` / `earliest`。


**参数校验极严, 而且会泄露 schema**: 服务端用 Zod 校验 query, **未知参数直接 400**。
所以 `?limit=20` 在 `topics` 上能用、在 `tags` 上就 400 —— 说明 `tags` 没这个参数。
而错误体的 `details` 等于接口说明书:

```
GET /api/v1/users/{uid}/posts?role=bogus
-> 400 {"details":{"role":["Invalid input: expected \"reply\""]}}

GET /api/v1/emojis
-> 400 {"details":{"collectionId":["Invalid input: expected string, received undefined"]}}

GET /api/v1/topics?cursor=abc
-> 400 {"code":"INVALID_CURSOR"}     # 专门的错误码, 说明 cursor 是已知参数只是值不对

GET /api/v1/topics?limit=5&kind=bogus
-> 400 {"details":{"kind":["Invalid option: expected one of \"DISCUSSION\"|\"QUESTION\""]}}
```

`kind` 只有 `topics` feed 支持 (`DISCUSSION` / `QUESTION`), `works` 和 `users/{uid}/topics`
加 `kind` 会 400 —— 用非法值问枚举, 比翻文档快。

## 内置护栏 (为什么这样设计)
1. **写操作强制间隔** (`--min-interval`, 默认 5s): 防止脚本瞬间狂发被风控。
2. **`--dry-run`**: 写操作只打印"将发送什么", 不真正发请求。建议第一次先用它确认。
3. **仅限本人 uid**: `list_user_topics` 等会比对登录账号 uid, 越权直接拦下。
4. **会话 Cookie 模式**: 登录后凭据由 cookiejar 保存, 不落盘、不打印密码。
5. **正文不含站外链接** (`find_external_links`): 服务端对 markdown 里的站外链接直接回
   `422 EXTERNAL_LINK_NOT_ALLOWED (field=markdown, host=<域名>)`, 整条写操作失败。
   本地自检放在**消耗水印序号之前**, 命中就抛错(带 hosts 与改写提示), 不发请求也不烧序号。
   - 提到外部项目时**只写项目名, 不写域名/URL**。
   - 带点的 API 名 (`entity.velocity` / `getGroupStorage` / `player.userKey`) 不算链接, 不会误伤。
   - 回归测试: `python test_link_guard.py` (23 项, 纯本地不发请求)。
6. **每日额度的通道口径**: 顶层评论占"评论"额度 (3/天), 楼中楼回复占"回复"额度 (12/天)。
   `reply --reply-to 1/0/None` 发出去的就是**顶层评论** (与 `comment` 服务端结构完全相同),
   必须查评论额度 —— 早期版本一律记回复额度, 等于"每日评论 ≤3"可被绕开到 12 条。
   计数以本地**账本** `.wm_ledger.json` 为准 (每条记录带 `channel` 字段),
   与 `.bot_state.json` 取较大值, 状态文件丢了也不会把上限清零。
   - 回归测试: `python test_channel_budget.py` (25 项) + `python test_daily_guard.py` (18 项)。
7. **账本主文件丢失/被清空时, 历史与硬上限都不能丢**:
   账本 `.wm_ledger.json` 是每日上限的权威来源, 另留一份 `.bak` 备份。
   - 读侧 (`ledger_read`) 主备**取记录更多**的那一份 —— 主文件缺失 (误删/被移动)、
     损坏 (半截 JSON)、或被写成合法的空列表 `[]`, 都能从备份恢复, 不会让当天
     已用的额度归零。
   - 写侧 (`ledger_append`) 基于读侧结果追加 —— 不会因为主文件不见了,
     就用一条新记录把备份里的历史覆盖掉。
   - 主备两份都在却都读不出 = 故障, 此时按"额度已满"处理 (fail closed)。
   - 备份路径由 `_ledger_paths()` 统一计算, 读写两侧一致 (多账号档案下同样生效)。
   - 回归测试: `python test_ledger_recovery.py` (22 项) + `python test_ledger_robustness.py` (24 项)。
   全套回归共 200 项, 改完跑一遍。

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
