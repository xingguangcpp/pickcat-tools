# API 实时状态 (自动生成)

> 由 `pickcat_probe.py` 于 **2026-11-02 20:17:21** 自动生成, 不要手改。
> 手写补充请放 README 的「已知 API 坑」一节; 本文件只反映**当前探测到的真实状态**。

- 服务可用: **是**
- 探测端点数: 23

| 端点 | 说明 | 鉴权 | 状态 | 耗时 | 条目 | 备注 |
|---|---|---|---|---|---|---|
| `GET /api/v1/topics?limit=5` | 社区帖子 feed | 公开 | ✅ 200 | 95ms | 5 |  |
| `GET /api/v1/topics/{tid}` | 主题详情 | 公开 | ✅ 200 | 142ms | None |  |
| `GET /api/v1/topics/{tid}/posts?limit=5` | 主题下的回复 | 公开 | ✅ 200 | 121ms | 0 |  |
| `GET /api/v1/topics/{tid}/posts?limit=5&sort=latest` | 主题回复(按最新排序) | 公开 | ✅ 200 | 115ms | 0 |  |
| `GET /api/v1/posts/{pid}` | 单条回复详情 | 公开 | ✅ 200 | 110ms | None |  |
| `GET /api/v1/tags` | 标签列表 | 公开 | ✅ 200 | 157ms | 11 |  |
| `GET /api/v1/tags?limit=20` | 标签带 limit(应被拒: 未知参数) | 公开 | ⚠️ 400 | 97ms | - | VALIDATION_FAILED |
| `GET /api/v1/users/{uid}` | 用户资料 | 公开 | ✅ 200 | 96ms | None |  |
| `GET /api/v1/users/{uid}/posts?role=reply&limit=5` | 用户回复(role=reply) | 公开 | ✅ 200 | 133ms | 5 |  |
| `GET /api/v1/users/{uid}/posts?role=bogus` | 用户回复(role 非法, 应报错) | 公开 | ⚠️ 400 | 96ms | - | VALIDATION_FAILED {"role": ["Invalid input: expected \"reply\""]} |
| `GET /api/v1/users/{uid}/followers?limit=5` | 粉丝列表 | 公开 | ✅ 200 | 110ms | 5 |  |
| `GET /api/v1/users/{uid}/following?limit=5` | 关注列表 | 公开 | ✅ 200 | 122ms | 2 |  |
| `GET /api/v1/notifications?limit=10` | 通知列表 | 公开 | ✅ 200 | 108ms | 10 |  |
| `GET /api/v1/works?limit=5` | 作品列表 | 公开 | ✅ 200 | 113ms | 5 |  |
| `GET /api/v1/badges` | 徽章列表 | 公开 | ✅ 200 | 109ms | 8 |  |
| `GET /api/v1/emoji-collections` | 表情包集列表 | 公开 | ✅ 200 | 112ms | 9 |  |
| `GET /api/v1/emojis?collectionId={cid}` | 表情包(需 collectionId) | 公开 | ✅ 200 | 100ms | 12 |  |
| `GET /api/v1/session` | 会话(需登录) | 需登录 | ✅ 200 | 119ms | None |  |
| `GET /api/v1/users/{uid}/topics?limit=5` | 我自己的主题(需登录) | 需登录 | ✅ 200 | 86ms | 5 |  |
| `GET /api/v1/post-submissions/{sid}` | 投稿审核状态(需登录) | 需登录 | ✅ 200 | 121ms | None |  |
| `GET /api/v1/__probe_nonexistent__` | 不存在的端点(应 404) | 公开 | ❌ 404 | 92ms | - | ROUTE_NOT_FOUND |
| `GET /api/v1/posts?limit=5` | posts 列表(已知不可用) | 公开 | ❌ 404 | 96ms | - | ROUTE_NOT_FOUND |
| `GET /api/v1/topics?cursor=abc` | 非法 cursor | 公开 | ⚠️ 400 | 92ms | - | INVALID_CURSOR |

## 怎么读这张表

- ✅ 200 = 端点可用
- ⚠️ 400 = **端点存在但参数不合法** (可继续深挖, `details` 里会泄露期望的 schema)
- ❌ 404 = 端点不存在

### 400 的价值

服务端用 Zod 校验 query 参数, 且**未知参数直接拒绝**。所以:

```
GET /api/v1/users/{uid}/posts?role=bogus
-> 400 {"details":{"role":["Invalid input: expected \\"reply\\""]}}
```

这条错误信息等于**接口的 schema 说明书** —— 枚举值、必填参数、类型都在里面。
反过来, 任何 query 参数一旦报 400 且 details 没提到它, 说明**这个参数名不存在**。
