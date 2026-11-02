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
