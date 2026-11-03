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

- `GET /api/v1/topics?limit=5` 列表项形态数 4 → **1** (服务端引入了新的数据形态)
- `GET /api/v1/notifications?limit=10` 列表项形态数 2 → **1** (服务端引入了新的数据形态)
- `GET /api/v1/notifications?limit=50` 列表项形态数 3 → **2** (服务端引入了新的数据形态)
- `GET /api/v1/topics?limit=5&sort=latest` 列表项形态数 4 → **1** (服务端引入了新的数据形态)
- `GET /api/v1/topics?limit=5&kind=DISCUSSION` 列表项形态数 4 → **1** (服务端引入了新的数据形态)
