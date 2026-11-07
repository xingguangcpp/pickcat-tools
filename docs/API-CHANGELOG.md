# API 变更日志 (自动生成)

> 由 `pickcat_probe.py` 维护。每次探测发现差异就追加一节。


## 2026-11-02 20:17:04

- **新增** `GET /api/v1/topics?limit=5` — 社区帖子 feed (HTTP 200)
- **新增** `GET /api/v1/topics/{tid}` — 主题详情 (HTTP 200)
- **新增** `GET /api/v1/topics/{tid}/posts?limit=5` — 主题下的回复 (HTTP 200)
- **新增** `GET /api/v1/topics/{tid}/posts?limit=5&sort=latest` — 主题回复(按最新排序) (HTTP 200)
- **新增** `GET /api/v1/posts/{pid}` — 单条回复详情 (HTTP 200)
- **新增** `GET /api/v1/tags` — 标签列表 (HTTP 200)
- **新增** `GET /api/v1/tags?limit=20` — 标签带 limit(应被拒: 未知参数) (HTTP 400)
- **新增** `GET /api/v1/users/{uid}` — 用户资料 (HTTP 200)
- **新增** `GET /api/v1/users/{uid}/posts?role=reply&limit=5` — 用户回复(role=reply) (HTTP 200)
- **新增** `GET /api/v1/users/{uid}/posts?role=bogus` — 用户回复(role 非法, 应报错) (HTTP 400)
- **新增** `GET /api/v1/users/{uid}/followers?limit=5` — 粉丝列表 (HTTP 200)
- **新增** `GET /api/v1/users/{uid}/following?limit=5` — 关注列表 (HTTP 200)
- **新增** `GET /api/v1/notifications?limit=10` — 通知列表 (HTTP 200)
- **新增** `GET /api/v1/works?limit=5` — 作品列表 (HTTP 200)
- **新增** `GET /api/v1/badges` — 徽章列表 (HTTP 200)
- **新增** `GET /api/v1/emoji-collections` — 表情包集列表 (HTTP 200)
- **新增** `GET /api/v1/emojis?collectionId={cid}` — 表情包(需 collectionId) (HTTP 200)
- **新增** `GET /api/v1/session` — 会话(需登录) (HTTP 200)
- **新增** `GET /api/v1/users/{uid}/topics?limit=5` — 我自己的主题(需登录) (HTTP 200)
- **新增** `GET /api/v1/post-submissions/{sid}` — 投稿审核状态(需登录) (HTTP 200)
- **新增** `GET /api/v1/__probe_nonexistent__` — 不存在的端点(应 404) (HTTP 404)
- **新增** `GET /api/v1/posts?limit=5` — posts 列表(已知不可用) (HTTP 404)
- **新增** `GET /api/v1/topics?cursor=abc` — 非法 cursor (HTTP 400)

## 2026-11-02 21:03:41

- **新增** `GET /api/v1/post-submissions?contentRole=TOPIC_REPLY&status=PENDING` — 投稿列表(需 contentRole+status) (HTTP 200)

## 2026-11-02 23:21:01

- `GET /api/v1/users/{uid}/following?limit=5` 条目数 2 → 3

## 2026-11-03 02:30:18

- **新增** `GET /api/v1/topics/{tid}/posts?limit=5&sort=bogus` — 主题回复 sort 非法(应泄露 sort 枚举) (HTTP 400)
- **新增** `GET /api/v1/users/{uid}/posts?role=reply&sort=bogus` — 用户回复加 sort(该端点无 sort 参数) (HTTP 400)
- **新增** `GET /api/v1/notifications?limit=100` — 通知 limit 超过上限(应泄露上限值) (HTTP 400)
- **新增** `GET /api/v1/notifications?limit=50` — 通知 limit 取上限(应 200) (HTTP 200)
- **新增** `GET /api/v1/topics?limit=5&sort=latest` — topics 带 sort(应支持) (HTTP 200)
- **新增** `GET /api/v1/users/{uid}/followers?limit=5&cursor=abc` — 粉丝列表带非法 cursor(应被拒) (HTTP 400)
- **新增** `GET /api/v1/emoji-collections/{cid}/emojis` — 表情包嵌套路径(已知不可用, 须用 collectionId) (HTTP 404)

## 2026-11-03 08:41:42

- **新增** `GET /api/v1/health` — 服务健康检查 (HTTP 200)
- **新增** `GET /api/v1/users/{uid}/badges` — 用户徽章 (HTTP 200)
- **新增** `GET /api/v1/works/{wid}` — 作品详情 (HTTP 200)
- **新增** `GET /api/v1/emoji-collections/{cid}` — 表情包集详情 (HTTP 200)
- **新增** `GET /api/v1/topics?limit=5&kind=bogus` — topics kind 非法(应泄露 kind 枚举) (HTTP 400)
- **新增** `GET /api/v1/topics?limit=5&kind=DISCUSSION` — topics kind=DISCUSSION(应 200) (HTTP 200)
- **新增** `GET /api/v1/works?limit=5&kind=DISCUSSION` — works 不支持 kind(应报错) (HTTP 400)
- **新增** `GET /api/v1/users/{uid}/topics?limit=5&kind=DISCUSSION` — 用户主题不支持 kind(应报错) (HTTP 400)
- **新增** `GET /api/v1/users/{uid}/followers?limit=abc` — 通知 limit 非数字(应报错) (HTTP 400)

## 2026-11-03 15:03:24

- **新增** `GET /api/v1/files?limit=1` — 文件列表(需登录) (HTTP 200)
- **新增** `GET /api/v1/files/{fid}` — 文件详情(公开可读原图) (HTTP 200)
- **新增** `GET /api/v1/files/00000000-0000-0000-0000-000000000000` — 文件不存在(应 404 FILE_NOT_FOUND) (HTTP 404)
- **新增** `GET /api/v1/works/{wid}/comments` — 作品评论列表 (HTTP 200)
- **新增** `GET /api/v1/works/{wid}/comments/1` — 作品评论(按楼号, 需登录) (HTTP 404)
- **新增** `GET /api/v1/works/{wid}/comments/0` — 作品评论楼号非法(应泄露下限) (HTTP 400)
- **新增** `GET /api/v1/users/{uid}/works?limit=1` — 用户作品列表 (HTTP 200)
- **新增** `GET /api/v1/badges/{bid}` — 徽章详情(id 是 slug 非 UUID) (HTTP 200)
- **新增** `GET /api/v1/badges/__probe_no_such_badge__` — 徽章不存在(应 404 BADGE_NOT_FOUND) (HTTP 404)
- **新增** `GET /api/v1/permissions` — 权限列表(需 access.manage) (HTTP 403)
- **新增** `GET /api/v1/users/{uid}/level` — 用户等级(需 community.level.manage) (HTTP 403)
- **新增** `GET /api/v1/emojis/{eid}` — 表情详情(需 community.emoji.manage) (HTTP 403)
- **新增** `GET /api/v1/topics?limit=51` — topics limit 超过上限(应为 50) (HTTP 400)
- **新增** `GET /api/v1/works?limit=51` — works limit 超过上限(应为 50) (HTTP 400)
- **新增** `GET /api/v1/users/{uid}/posts?role=reply&limit=51` — 用户回复 limit 超过上限(应为 50) (HTTP 400)
- **新增** `GET /api/v1/topics/{tid}/posts?limit=101` — 主题回复 limit 超过上限(应为 100) (HTTP 400)
- **新增** `GET /api/v1/files?limit=101` — 文件 limit 超过上限(应为 100) (HTTP 400)
- **新增** `GET /api/v1/users/{uid}/works?limit=51` — 用户作品 limit 超过上限(应为 50) (HTTP 400)
- **新增** `GET /api/v1/topics?limit=5&sort=bogus` — topics feed sort 非法(应泄露 relevance|latest) (HTTP 400)
- **新增** `GET /api/v1/topics?limit=5&sort=relevance` — topics sort=relevance(浏览模式应被拒) (HTTP 400)
- **新增** `GET /api/v1/works/{wid}/comments?sort=bogus` — 作品评论 sort 非法(应泄露 hot|latest|earliest) (HTTP 400)
- **新增** `GET /api/v1/emoji-collections?limit=5` — 表情包集带 limit(该端点无此参数) (HTTP 400)
- **新增** `GET /api/v1/emojis?collectionId={cid}&limit=5` — 表情包带 limit(该端点无此参数) (HTTP 400)
- **新增** `GET /api/v1/badges?limit=1` — 徽章带 limit(该端点接受但不生效) (HTTP 200)

## 2026-11-03 15:05:07

- `/api/v1/works/{wid}/comments/0` 探针表标注变化: 公开 → **需登录**
- `/api/v1/works/{wid}/comments/0` 标注与实测现已一致 (需登录)

## 2026-11-03 18:19:32

- 📐 **快照格式升级** v1(无版本) → v2: 结构记录改为完整顶层键 + 字段路径 + 列表项签名 (本轮不逐项报结构差异, 新基线从下一轮开始生效)

## 2026-11-03 18:26:17

- 📐 **快照格式升级** v2 → v3: 列表项签名改为"形态集合"(与内容无关, 只看键名) + 独立类型表

## 2026-11-03 18:28:25

- 📐 **快照格式升级** 2 → v3: 结构记录改为完整顶层键 + 字段路径 + 列表项签名 (本轮不逐项报结构差异, 新基线从下一轮开始生效)

> **更正**: 上面 18:26 与 18:28 两节里的"列表项形态数 4 → 1"以及
> 18:32 的"消失 `questionState`"**都是巡检器自身的误报, 不是服务端变化**。
> 原因: 最初那版把"字段类型"混进了形态判定, 于是内容里的 `null` 值一变就抖动;
> 后来一版按形态集合直接对比, 而 `sort=latest` 是**滑动窗口** ——
> 窗口里一个 QUESTION 类主题被新主题顶出去, 形态集合就变了, 服务端却没变。
> 现已改为: 类型单独记且 null 当"不知道"; 形态差分只比**所有形态都有的字段**(交集),
> 窗口内容怎么变都不再误报。这两条误报记录保留在此, 作为"工具自身出过什么问题"的痕迹。

## 2026-11-03 21:48:40

- `GET /api/v1/notifications?limit=10` 列表项形态变化: 消失 `details.post`

> **更正**: 上面 21:48 这条"消失 `details.post`"**是巡检器自身的误报, 不是服务端变化**。
> 根因: 通知的 `details` 字段**由 `type` 决定** ——
>   `REPLY_CREATED`/`POST_LIKED` → `details.actor + details.post + details.topic`
>   `USER_MENTIONED` → 再加 `details.excerpt`
>   `TOPIC_EVENT` → `details.actor + details.event + details.topic` (**没有 post**)
>   `USER_FOLLOWED` → `details.actor + details.userFollowId` (**没有 post**)
> 旧窗口恰好只装得下带 `post` 的类型, 新窗口混进一条 `TOPIC_EVENT`,
> 于是"所有形态求交集"的旧口径一缩 —— 就报成了"服务端删了 `details.post`"。
> 服务端一个字段都没动, 变的只是"这轮通知列表里恰好有哪几种类型"。
>
> **为什么上一版的修法没挡住**: v3 改成"形态差分只比所有形态都有的字段(交集)",
> 那挡住了"窗口里**少**一种形态"(集合缩小, 交集不变),
> 却挡不住"窗口里**多**一种形态" —— 多一种形态, 交集反而变小, 于是误报。
> 集合本身是内容相关的, 拿它做差分, 两个方向都会漏。
>
> **v4 的修法**: 改成**按判别字段(type/kind)分组, 组内比**。
> `TOPIC_EVENT` 是独立的一组, 不影响 `REPLY_CREATED` 组 —— 内容抖动被隔离在组内。
> 组内用 must×may 交叉口径 (must=每条都有的字段, may=至少一条有的字段):
> "消失" = 旧 must 有且新 may 都没有, "新增" = 新 must 有且旧 may 都没有。
>
> 另外补了一个坑: **分组维度必须只依赖端点 schema, 不能依赖窗口内容**。
> 曾要求"取值种类 >= 2"才算判别字段, 但窗口里恰好只有一种类型时
> (实测 `/users/{uid}/topics` 那轮只有 `kind=DISCUSSION`), 维度会从 `kind` 翻成 None ——
> 两轮组配不上, 凭空多出"分组维度变化"这种假警报。
> 现在维度**锁存**在 `.probe_discriminators.json`, 跨轮次沿用。
>
> 代价(明确记下来): 服务端**新增一种通知类型**这类变化会被漏掉
> (新组只在一边出现, 按"只比两边都有的组"的口径不报)。
> 宁可漏报不要天天误报 —— 新组仍完整记在 `api-snapshot.json` 与 `API-STATUS.md` 里。

## 2026-11-03 22:16:38

- 📐 **快照格式升级** 3 → v4: 结构记录改为完整顶层键 + 字段路径 + 列表项形态集合 + 分组结构 (本轮不逐项报结构差异, 新基线从下一轮开始生效)

## 2026-11-04 21:26:06

- **新增** `GET /api/v1/topics/00000000-0000-0000-0000-000000000000` — 主题不存在(应 404 TOPIC_NOT_FOUND) (HTTP 404)
- **新增** `GET /api/v1/users/00000000-0000-0000-0000-000000000000` — 用户不存在(应 404 USER_NOT_FOUND) (HTTP 404)
- **新增** `GET /api/v1/works/00000000-0000-0000-0000-000000000000` — 作品不存在(应 404 COMMUNITY_WORK_NOT_FOUND) (HTTP 404)
- **新增** `GET /api/v1/emoji-collections/00000000-0000-0000-0000-000000000000` — 表情包集不存在(应 404 EMOJI_COLLECTION_NOT_FOUND) (HTTP 404)
- **新增** `GET /api/v1/posts/00000000-0000-0000-0000-000000000000` — 回复不存在(应 404 POST_NOT_FOUND, 需登录) (HTTP 404)
- **新增** `GET /api/v1/works/{fw}/comments/9999` — 作品评论不存在(应 404 WORK_COMMENT_NOT_FOUND, 需登录) (HTTP 404)
- 📐 **快照格式升级** 4 → v5: 结构记录改为完整顶层键 + 字段路径 + 列表项形态集合 + 分组结构 (本轮不逐项报结构差异, 新基线从下一轮开始生效)

## 2026-11-05 20:47:56

- `GET /api/v1/session` 字段路径变化: 消失 `silence.publicReasonDetail`, `silence.until`, `user.silence`

## 2026-11-06 20:00:21

- `GET /api/v1/topics/{tid}` 字段路径变化: 新增 `viewerCapabilities.canRequestDuplicate`

## 2026-11-07 18:07:51

- `GET /api/v1/users/{uid}/following?limit=5` 条目数 3 → 4
