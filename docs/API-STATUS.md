# API 实时状态 (自动生成)

> 由 `pickcat_probe.py` 于 **2026-11-03 15:03:24** 自动生成, 不要手改。
> 手写补充请放 README 的「已知 API 坑」一节; 本文件只反映**当前探测到的真实状态**。

- 服务可用: **是**
- 探测端点数: 64
- 已实测鉴权的路由数: 37

| 端点 | 说明 | 鉴权 | 状态 | 耗时 | 条目 | 备注 |
|---|---|---|---|---|---|---|
| `GET /api/v1/topics?limit=5` | 社区帖子 feed | 公开 | ✅ 200 | 87ms | 5 |  |
| `GET /api/v1/topics/{tid}` | 主题详情 | 公开 | ✅ 200 | 195ms | None |  |
| `GET /api/v1/topics/{tid}/posts?limit=5` | 主题下的回复 | 公开 | ✅ 200 | 96ms | 0 |  |
| `GET /api/v1/topics/{tid}/posts?limit=5&sort=latest` | 主题回复(按最新排序) | 公开 | ✅ 200 | 139ms | 0 |  |
| `GET /api/v1/posts/{pid}` | 单条回复详情 | 需登录 | ✅ 200 | 1141ms | None |  |
| `GET /api/v1/tags` | 标签列表 | 公开 | ✅ 200 | 86ms | 11 |  |
| `GET /api/v1/tags?limit=20` | 标签带 limit(应被拒: 未知参数) | 公开 | ⚠️ 400 | 92ms | - | VALIDATION_FAILED |
| `GET /api/v1/users/{uid}` | 用户资料(含等级/统计) | 公开 | ✅ 200 | 122ms | None |  |
| `GET /api/v1/post-submissions?contentRole=TOPIC_REPLY&status=PENDING` | 投稿列表(需 contentRole+status) | 需登录 | ✅ 200 | 193ms | 0 |  |
| `GET /api/v1/users/{uid}/posts?role=reply&limit=5` | 用户回复(role=reply) | 公开 | ✅ 200 | 111ms | 5 |  |
| `GET /api/v1/users/{uid}/posts?role=bogus` | 用户回复(role 非法, 应报错) | 公开 | ⚠️ 400 | 115ms | - | VALIDATION_FAILED {"role": ["Invalid input: expected \"reply\""]} |
| `GET /api/v1/users/{uid}/followers?limit=5` | 粉丝列表 | 公开 | ✅ 200 | 76ms | 5 |  |
| `GET /api/v1/users/{uid}/following?limit=5` | 关注列表 | 公开 | ✅ 200 | 145ms | 3 |  |
| `GET /api/v1/notifications?limit=10` | 通知列表 | 需登录 | ✅ 200 | 109ms | 10 |  |
| `GET /api/v1/works?limit=5` | 作品列表 | 公开 | ✅ 200 | 532ms | 5 |  |
| `GET /api/v1/badges` | 徽章列表 | 公开 | ✅ 200 | 112ms | 8 |  |
| `GET /api/v1/emoji-collections` | 表情包集列表 | 公开 | ✅ 200 | 75ms | 9 |  |
| `GET /api/v1/emojis?collectionId={cid}` | 表情包(需 collectionId) | 公开 | ✅ 200 | 90ms | 12 |  |
| `GET /api/v1/session` | 会话(需登录) | 需登录 | ✅ 200 | 104ms | None |  |
| `GET /api/v1/users/{uid}/topics?limit=5` | 我自己的主题(需登录) | 公开 | ✅ 200 | 136ms | 5 |  |
| `GET /api/v1/post-submissions/{sid}` | 投稿审核状态(需登录) | 需登录 | ✅ 200 | 97ms | None |  |
| `GET /api/v1/__probe_nonexistent__` | 不存在的端点(应 404) | 不存在 | ❌ 404 | 118ms | - | ROUTE_NOT_FOUND |
| `GET /api/v1/posts?limit=5` | posts 列表(已知不可用) | 不存在 | ❌ 404 | 64ms | - | ROUTE_NOT_FOUND |
| `GET /api/v1/topics?cursor=abc` | 非法 cursor | 公开 | ⚠️ 400 | 91ms | - | INVALID_CURSOR |
| `GET /api/v1/topics/{tid}/posts?limit=5&sort=bogus` | 主题回复 sort 非法(应泄露 sort 枚举) | 公开 | ⚠️ 400 | 77ms | - | VALIDATION_FAILED {"sort": ["Invalid option: expected one of \"hot\"|\"latest\"|\"earliest\""]} |
| `GET /api/v1/users/{uid}/posts?role=reply&sort=bogus` | 用户回复加 sort(该端点无 sort 参数) | 公开 | ⚠️ 400 | 71ms | - | VALIDATION_FAILED |
| `GET /api/v1/notifications?limit=100` | 通知 limit 超过上限(应泄露上限值) | 需登录 | ⚠️ 400 | 75ms | - | VALIDATION_FAILED {"limit": ["Too big: expected number to be <=50"]} |
| `GET /api/v1/notifications?limit=50` | 通知 limit 取上限(应 200) | 需登录 | ✅ 200 | 130ms | 50 |  |
| `GET /api/v1/topics?limit=5&sort=latest` | topics 带 sort(应支持) | 公开 | ✅ 200 | 86ms | 5 |  |
| `GET /api/v1/users/{uid}/followers?limit=5&cursor=abc` | 粉丝列表带非法 cursor(应被拒) | 公开 | ⚠️ 400 | 67ms | - | VALIDATION_FAILED {"cursor": ["游标不合法"]} |
| `GET /api/v1/emoji-collections/{cid}/emojis` | 表情包嵌套路径(已知不可用, 须用 collectionId) | 不存在 | ❌ 404 | 125ms | - | ROUTE_NOT_FOUND |
| `GET /api/v1/health` | 服务健康检查 | 公开 | ✅ 200 | 117ms | None |  |
| `GET /api/v1/users/{uid}/badges` | 用户徽章 | 公开 | ✅ 200 | 86ms | 1 |  |
| `GET /api/v1/works/{wid}` | 作品详情 | 公开 | ✅ 200 | 97ms | None |  |
| `GET /api/v1/emoji-collections/{cid}` | 表情包集详情 | 公开 | ✅ 200 | 86ms | None |  |
| `GET /api/v1/topics?limit=5&kind=bogus` | topics kind 非法(应泄露 kind 枚举) | 公开 | ⚠️ 400 | 115ms | - | VALIDATION_FAILED {"kind": ["Invalid option: expected one of \"DISCUSSION\"|\"QUESTION\""]} |
| `GET /api/v1/topics?limit=5&kind=DISCUSSION` | topics kind=DISCUSSION(应 200) | 公开 | ✅ 200 | 102ms | 5 |  |
| `GET /api/v1/works?limit=5&kind=DISCUSSION` | works 不支持 kind(应报错) | 公开 | ⚠️ 400 | 77ms | - | VALIDATION_FAILED |
| `GET /api/v1/users/{uid}/topics?limit=5&kind=DISCUSSION` | 用户主题不支持 kind(应报错) | 公开 | ⚠️ 400 | 74ms | - | VALIDATION_FAILED |
| `GET /api/v1/users/{uid}/followers?limit=abc` | 通知 limit 非数字(应报错) | 公开 | ⚠️ 400 | 69ms | - | VALIDATION_FAILED {"limit": ["Invalid input: expected number, received NaN"]} |
| `GET /api/v1/files?limit=1` | 文件列表(需登录) | 需登录 | ✅ 200 | 339ms | 1 |  |
| `GET /api/v1/files/{fid}` | 文件详情(公开可读原图) | 公开 | ✅ 200 | 238ms | None |  |
| `GET /api/v1/files/00000000-0000-0000-0000-000000000000` | 文件不存在(应 404 FILE_NOT_FOUND) | 不存在 | ❌ 404 | 108ms | - | FILE_NOT_FOUND |
| `GET /api/v1/works/{wid}/comments` | 作品评论列表 | 公开 | ✅ 200 | 81ms | 0 |  |
| `GET /api/v1/works/{wid}/comments/1` | 作品评论(按楼号, 需登录) | 需登录 | ❌ 404 | 74ms | - | WORK_COMMENT_NOT_FOUND |
| `GET /api/v1/works/{wid}/comments/0` | 作品评论楼号非法(应泄露下限) | 需登录 | ⚠️ 400 | 74ms | - | VALIDATION_FAILED {"commentNumber": ["Too small: expected number to be >=1"]} |
| `GET /api/v1/users/{uid}/works?limit=1` | 用户作品列表 | 公开 | ✅ 200 | 99ms | 1 |  |
| `GET /api/v1/badges/{bid}` | 徽章详情(id 是 slug 非 UUID) | 公开 | ✅ 200 | 138ms | None |  |
| `GET /api/v1/badges/__probe_no_such_badge__` | 徽章不存在(应 404 BADGE_NOT_FOUND) | 不存在 | ❌ 404 | 72ms | - | BADGE_NOT_FOUND |
| `GET /api/v1/permissions` | 权限列表(需 access.manage) | 需登录 | ❌ 403 | 120ms | - | PERMISSION_DENIED {"permissionKey": "access.manage"} |
| `GET /api/v1/users/{uid}/level` | 用户等级(需 community.level.manage) | 需登录 | ❌ 403 | 97ms | - | PERMISSION_DENIED {"permissionKey": "community.level.manage"} |
| `GET /api/v1/emojis/{eid}` | 表情详情(需 community.emoji.manage) | 需登录 | ❌ 403 | 109ms | - | PERMISSION_DENIED {"permissionKey": "community.emoji.manage"} |
| `GET /api/v1/topics?limit=51` | topics limit 超过上限(应为 50) | 公开 | ⚠️ 400 | 91ms | - | VALIDATION_FAILED {"limit": ["Too big: expected number to be <=50"]} |
| `GET /api/v1/works?limit=51` | works limit 超过上限(应为 50) | 公开 | ⚠️ 400 | 69ms | - | VALIDATION_FAILED {"limit": ["Too big: expected number to be <=50"]} |
| `GET /api/v1/users/{uid}/posts?role=reply&limit=51` | 用户回复 limit 超过上限(应为 50) | 公开 | ⚠️ 400 | 103ms | - | VALIDATION_FAILED {"limit": ["Too big: expected number to be <=50"]} |
| `GET /api/v1/topics/{tid}/posts?limit=101` | 主题回复 limit 超过上限(应为 100) | 公开 | ⚠️ 400 | 86ms | - | VALIDATION_FAILED {"limit": ["Too big: expected number to be <=100"]} |
| `GET /api/v1/files?limit=101` | 文件 limit 超过上限(应为 100) | 需登录 | ⚠️ 400 | 104ms | - | VALIDATION_FAILED {"limit": ["Too big: expected number to be <=100"]} |
| `GET /api/v1/users/{uid}/works?limit=51` | 用户作品 limit 超过上限(应为 50) | 公开 | ⚠️ 400 | 87ms | - | VALIDATION_FAILED {"limit": ["Too big: expected number to be <=50"]} |
| `GET /api/v1/topics?limit=5&sort=bogus` | topics feed sort 非法(应泄露 relevance|latest) | 公开 | ⚠️ 400 | 78ms | - | VALIDATION_FAILED {"sort": ["Invalid option: expected one of \"relevance\"|\"latest\""]} |
| `GET /api/v1/topics?limit=5&sort=relevance` | topics sort=relevance(浏览模式应被拒) | 公开 | ⚠️ 400 | 272ms | - | VALIDATION_FAILED {"sort": ["浏览模式不能按相关性排序"]} |
| `GET /api/v1/works/{wid}/comments?sort=bogus` | 作品评论 sort 非法(应泄露 hot|latest|earliest) | 公开 | ⚠️ 400 | 427ms | - | VALIDATION_FAILED {"sort": ["Invalid option: expected one of \"hot\"|\"latest\"|\"earliest\""]} |
| `GET /api/v1/emoji-collections?limit=5` | 表情包集带 limit(该端点无此参数) | 公开 | ⚠️ 400 | 97ms | - | VALIDATION_FAILED |
| `GET /api/v1/emojis?collectionId={cid}&limit=5` | 表情包带 limit(该端点无此参数) | 公开 | ⚠️ 400 | 66ms | - | VALIDATION_FAILED |
| `GET /api/v1/badges?limit=1` | 徽章带 limit(该端点接受但不生效) | 公开 | ✅ 200 | 97ms | 8 |  |

## 怎么读这张表

- ✅ 200 = 端点可用
- ⚠️ 400 = **端点存在但参数不合法** (可继续深挖, `details` 里会泄露期望的 schema)
- ❌ 404 = 端点不存在

### 鉴权列是**实测**的, 不是手写的

每条路由都会**不带 cookie** 再打一次, 据此判定它到底公开还是要登录:

| 匿名结果 | 判定 | 含义 |
|---|---|---|
| 200 | 公开 | 不用登录就能读 |
| 401 | 需登录 | 路由存在, 差一个 cookie |
| 403 | 需权限 | 登录了但权限不够 (如社区管理) |
| 400 | 公开 | 鉴权这关过了, 只是参数不合法 |
| 404 | 不存在 | 路由本身没有 |

服务端哪天把一个公开端点改成需登录, 这里会立刻变成 ⚠️ 并记进 CHANGELOG。

### 400 的价值

服务端用 Zod 校验 query 参数, 且**未知参数直接拒绝**。所以:

```
GET /api/v1/users/{uid}/posts?role=bogus
-> 400 {"details":{"role":["Invalid input: expected \\"reply\\""]}}
```

这条错误信息等于**接口的 schema 说明书** —— 枚举值、必填参数、类型都在里面。
反过来, 任何 query 参数一旦报 400 且 details 没提到它, 说明**这个参数名不存在**。
