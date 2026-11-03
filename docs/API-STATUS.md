# API 实时状态 (自动生成)

> 由 `pickcat_probe.py` 于 **2026-11-03 22:16:38** 自动生成, 不要手改。
> 手写补充请放 README 的「已知 API 坑」一节; 本文件只反映**当前探测到的真实状态**。

- 服务可用: **是**
- 探测端点数: 64
- 已实测鉴权的路由数: 37
- 快照格式版本: v4

| 端点 | 说明 | 鉴权 | 状态 | 耗时 | 条目 | 备注 |
|---|---|---|---|---|---|---|
| `GET /api/v1/topics?limit=5` | 社区帖子 feed | 公开 | ✅ 200 | 84ms | 5 |  |
| `GET /api/v1/topics/{tid}` | 主题详情 | 公开 | ✅ 200 | 121ms | None |  |
| `GET /api/v1/topics/{tid}/posts?limit=5` | 主题下的回复 | 公开 | ✅ 200 | 84ms | 0 |  |
| `GET /api/v1/topics/{tid}/posts?limit=5&sort=latest` | 主题回复(按最新排序) | 公开 | ✅ 200 | 114ms | 0 |  |
| `GET /api/v1/posts/{pid}` | 单条回复详情 | 需登录 | ✅ 200 | 109ms | None |  |
| `GET /api/v1/tags` | 标签列表 | 公开 | ✅ 200 | 109ms | 11 |  |
| `GET /api/v1/tags?limit=20` | 标签带 limit(应被拒: 未知参数) | 公开 | ⚠️ 400 | 65ms | - | VALIDATION_FAILED |
| `GET /api/v1/users/{uid}` | 用户资料(含等级/统计) | 公开 | ✅ 200 | 153ms | None |  |
| `GET /api/v1/post-submissions?contentRole=TOPIC_REPLY&status=PENDING` | 投稿列表(需 contentRole+status) | 需登录 | ✅ 200 | 76ms | 0 |  |
| `GET /api/v1/users/{uid}/posts?role=reply&limit=5` | 用户回复(role=reply) | 公开 | ✅ 200 | 88ms | 5 |  |
| `GET /api/v1/users/{uid}/posts?role=bogus` | 用户回复(role 非法, 应报错) | 公开 | ⚠️ 400 | 105ms | - | VALIDATION_FAILED {"role": ["Invalid input: expected \"reply\""]} |
| `GET /api/v1/users/{uid}/followers?limit=5` | 粉丝列表 | 公开 | ✅ 200 | 125ms | 5 |  |
| `GET /api/v1/users/{uid}/following?limit=5` | 关注列表 | 公开 | ✅ 200 | 110ms | 3 |  |
| `GET /api/v1/notifications?limit=10` | 通知列表 | 需登录 | ✅ 200 | 105ms | 10 |  |
| `GET /api/v1/works?limit=5` | 作品列表 | 公开 | ✅ 200 | 104ms | 5 |  |
| `GET /api/v1/badges` | 徽章列表 | 公开 | ✅ 200 | 125ms | 8 |  |
| `GET /api/v1/emoji-collections` | 表情包集列表 | 公开 | ✅ 200 | 100ms | 9 |  |
| `GET /api/v1/emojis?collectionId={cid}` | 表情包(需 collectionId) | 公开 | ✅ 200 | 100ms | 12 |  |
| `GET /api/v1/session` | 会话(需登录) | 需登录 | ✅ 200 | 115ms | None |  |
| `GET /api/v1/users/{uid}/topics?limit=5` | 我自己的主题(需登录) | 公开 | ✅ 200 | 108ms | 5 |  |
| `GET /api/v1/post-submissions/{sid}` | 投稿审核状态(需登录) | 需登录 | ✅ 200 | 127ms | None |  |
| `GET /api/v1/__probe_nonexistent__` | 不存在的端点(应 404) | 不存在 | ❌ 404 | 71ms | - | ROUTE_NOT_FOUND |
| `GET /api/v1/posts?limit=5` | posts 列表(已知不可用) | 不存在 | ❌ 404 | 86ms | - | ROUTE_NOT_FOUND |
| `GET /api/v1/topics?cursor=abc` | 非法 cursor | 公开 | ⚠️ 400 | 75ms | - | INVALID_CURSOR |
| `GET /api/v1/topics/{tid}/posts?limit=5&sort=bogus` | 主题回复 sort 非法(应泄露 sort 枚举) | 公开 | ⚠️ 400 | 90ms | - | VALIDATION_FAILED {"sort": ["Invalid option: expected one of \"hot\"|\"latest\"|\"earliest\""]} |
| `GET /api/v1/users/{uid}/posts?role=reply&sort=bogus` | 用户回复加 sort(该端点无 sort 参数) | 公开 | ⚠️ 400 | 80ms | - | VALIDATION_FAILED |
| `GET /api/v1/notifications?limit=100` | 通知 limit 超过上限(应泄露上限值) | 需登录 | ⚠️ 400 | 94ms | - | VALIDATION_FAILED {"limit": ["Too big: expected number to be <=50"]} |
| `GET /api/v1/notifications?limit=50` | 通知 limit 取上限(应 200) | 需登录 | ✅ 200 | 126ms | 50 |  |
| `GET /api/v1/topics?limit=5&sort=latest` | topics 带 sort(应支持) | 公开 | ✅ 200 | 91ms | 5 |  |
| `GET /api/v1/users/{uid}/followers?limit=5&cursor=abc` | 粉丝列表带非法 cursor(应被拒) | 公开 | ⚠️ 400 | 189ms | - | VALIDATION_FAILED {"cursor": ["游标不合法"]} |
| `GET /api/v1/emoji-collections/{cid}/emojis` | 表情包嵌套路径(已知不可用, 须用 collectionId) | 不存在 | ❌ 404 | 95ms | - | ROUTE_NOT_FOUND |
| `GET /api/v1/health` | 服务健康检查 | 公开 | ✅ 200 | 154ms | None |  |
| `GET /api/v1/users/{uid}/badges` | 用户徽章 | 公开 | ✅ 200 | 122ms | 1 |  |
| `GET /api/v1/works/{wid}` | 作品详情 | 公开 | ✅ 200 | 103ms | None |  |
| `GET /api/v1/emoji-collections/{cid}` | 表情包集详情 | 公开 | ✅ 200 | 136ms | None |  |
| `GET /api/v1/topics?limit=5&kind=bogus` | topics kind 非法(应泄露 kind 枚举) | 公开 | ⚠️ 400 | 68ms | - | VALIDATION_FAILED {"kind": ["Invalid option: expected one of \"DISCUSSION\"|\"QUESTION\""]} |
| `GET /api/v1/topics?limit=5&kind=DISCUSSION` | topics kind=DISCUSSION(应 200) | 公开 | ✅ 200 | 95ms | 5 |  |
| `GET /api/v1/works?limit=5&kind=DISCUSSION` | works 不支持 kind(应报错) | 公开 | ⚠️ 400 | 95ms | - | VALIDATION_FAILED |
| `GET /api/v1/users/{uid}/topics?limit=5&kind=DISCUSSION` | 用户主题不支持 kind(应报错) | 公开 | ⚠️ 400 | 81ms | - | VALIDATION_FAILED |
| `GET /api/v1/users/{uid}/followers?limit=abc` | 通知 limit 非数字(应报错) | 公开 | ⚠️ 400 | 94ms | - | VALIDATION_FAILED {"limit": ["Invalid input: expected number, received NaN"]} |
| `GET /api/v1/files?limit=1` | 文件列表(需登录) | 需登录 | ✅ 200 | 116ms | 1 |  |
| `GET /api/v1/files/{fid}` | 文件详情(公开可读原图) | 公开 | ✅ 200 | 147ms | None |  |
| `GET /api/v1/files/00000000-0000-0000-0000-000000000000` | 文件不存在(应 404 FILE_NOT_FOUND) | 不存在 | ❌ 404 | 323ms | - | FILE_NOT_FOUND |
| `GET /api/v1/works/{wid}/comments` | 作品评论列表 | 公开 | ✅ 200 | 97ms | 0 |  |
| `GET /api/v1/works/{wid}/comments/1` | 作品评论(按楼号, 需登录) | 需登录 | ❌ 404 | 71ms | - | WORK_COMMENT_NOT_FOUND |
| `GET /api/v1/works/{wid}/comments/0` | 作品评论楼号非法(应泄露下限) | 需登录 | ⚠️ 400 | 92ms | - | VALIDATION_FAILED {"commentNumber": ["Too small: expected number to be >=1"]} |
| `GET /api/v1/users/{uid}/works?limit=1` | 用户作品列表 | 公开 | ✅ 200 | 152ms | 1 |  |
| `GET /api/v1/badges/{bid}` | 徽章详情(id 是 slug 非 UUID) | 公开 | ✅ 200 | 101ms | None |  |
| `GET /api/v1/badges/__probe_no_such_badge__` | 徽章不存在(应 404 BADGE_NOT_FOUND) | 不存在 | ❌ 404 | 102ms | - | BADGE_NOT_FOUND |
| `GET /api/v1/permissions` | 权限列表(需 access.manage) | 需登录 | ❌ 403 | 101ms | - | PERMISSION_DENIED {"permissionKey": "access.manage"} |
| `GET /api/v1/users/{uid}/level` | 用户等级(需 community.level.manage) | 需登录 | ❌ 403 | 75ms | - | PERMISSION_DENIED {"permissionKey": "community.level.manage"} |
| `GET /api/v1/emojis/{eid}` | 表情详情(需 community.emoji.manage) | 需登录 | ❌ 403 | 104ms | - | PERMISSION_DENIED {"permissionKey": "community.emoji.manage"} |
| `GET /api/v1/topics?limit=51` | topics limit 超过上限(应为 50) | 公开 | ⚠️ 400 | 108ms | - | VALIDATION_FAILED {"limit": ["Too big: expected number to be <=50"]} |
| `GET /api/v1/works?limit=51` | works limit 超过上限(应为 50) | 公开 | ⚠️ 400 | 104ms | - | VALIDATION_FAILED {"limit": ["Too big: expected number to be <=50"]} |
| `GET /api/v1/users/{uid}/posts?role=reply&limit=51` | 用户回复 limit 超过上限(应为 50) | 公开 | ⚠️ 400 | 83ms | - | VALIDATION_FAILED {"limit": ["Too big: expected number to be <=50"]} |
| `GET /api/v1/topics/{tid}/posts?limit=101` | 主题回复 limit 超过上限(应为 100) | 公开 | ⚠️ 400 | 75ms | - | VALIDATION_FAILED {"limit": ["Too big: expected number to be <=100"]} |
| `GET /api/v1/files?limit=101` | 文件 limit 超过上限(应为 100) | 需登录 | ⚠️ 400 | 65ms | - | VALIDATION_FAILED {"limit": ["Too big: expected number to be <=100"]} |
| `GET /api/v1/users/{uid}/works?limit=51` | 用户作品 limit 超过上限(应为 50) | 公开 | ⚠️ 400 | 94ms | - | VALIDATION_FAILED {"limit": ["Too big: expected number to be <=50"]} |
| `GET /api/v1/topics?limit=5&sort=bogus` | topics feed sort 非法(应泄露 relevance|latest) | 公开 | ⚠️ 400 | 92ms | - | VALIDATION_FAILED {"sort": ["Invalid option: expected one of \"relevance\"|\"latest\""]} |
| `GET /api/v1/topics?limit=5&sort=relevance` | topics sort=relevance(浏览模式应被拒) | 公开 | ⚠️ 400 | 145ms | - | VALIDATION_FAILED {"sort": ["浏览模式不能按相关性排序"]} |
| `GET /api/v1/works/{wid}/comments?sort=bogus` | 作品评论 sort 非法(应泄露 hot|latest|earliest) | 公开 | ⚠️ 400 | 66ms | - | VALIDATION_FAILED {"sort": ["Invalid option: expected one of \"hot\"|\"latest\"|\"earliest\""]} |
| `GET /api/v1/emoji-collections?limit=5` | 表情包集带 limit(该端点无此参数) | 公开 | ⚠️ 400 | 86ms | - | VALIDATION_FAILED |
| `GET /api/v1/emojis?collectionId={cid}&limit=5` | 表情包带 limit(该端点无此参数) | 公开 | ⚠️ 400 | 111ms | - | VALIDATION_FAILED |
| `GET /api/v1/badges?limit=1` | 徽章带 limit(该端点接受但不生效) | 公开 | ✅ 200 | 89ms | 8 |  |

## 响应结构 (实测字段指纹)

> 自动生成: 下面是各端点**实际返回**的字段路径 + 类型。
> 服务端改字段名/改类型/删字段, 巡检会立刻报出来并记进 CHANGELOG。
> 深度: 顶层 + 再进一层对象 (如 `author.*` / `stats.*` / `firstPost.*`)。

### `GET /api/v1/topics?limit=5`

顶层字段:

```
items:array
pageInfo.hasNextPage:bool
pageInfo.nextCursor:str
pageInfo:object
```

列表项形态 (2 种, 数据形态数与内容无关):

形态 1:

```
author:object
author.avatar:object
author.id:str
author.username:str
bookmarkCount:int
closedAt:null
createdAt:str
editedAt:null
excerpt:str
id:str
kind:str
lastActivityAt:str
likeCount:int
pinned:bool
pinnedAt:null
pinnedGlobally:bool
pinnedTagId:null
pinnedUntil:null
questionState:object
questionState.resolution:null
replyCount:int
tags:array
title:str
viewCount:int
```

形态 2:

```
author:object
author.avatar:object
author.id:str
author.username:str
bookmarkCount:int
closedAt:null
createdAt:str
editedAt:null
excerpt:str
id:str
kind:str
lastActivityAt:str
likeCount:int
pinned:bool
pinnedAt:null
pinnedGlobally:bool
pinnedTagId:null
pinnedUntil:null
replyCount:int
tags:array
title:str
viewCount:int
```

按 `kind` 分组 (2 组, 每组字段是该类项**一定有**的字段):

组 `DISCUSSION`:

```
author:object
author.avatar:object
author.id:str
author.username:str
bookmarkCount:int
closedAt:null
createdAt:str
editedAt:null
excerpt:str
id:str
kind:str
lastActivityAt:str
likeCount:int
pinned:bool
pinnedAt:str
pinnedGlobally:bool
pinnedTagId:null
pinnedUntil:null
replyCount:int
tags:array
title:str
viewCount:int
```

组 `QUESTION`:

```
author:object
author.avatar:object
author.id:str
author.username:str
bookmarkCount:int
closedAt:null
createdAt:str
editedAt:null
excerpt:str
id:str
kind:str
lastActivityAt:str
likeCount:int
pinned:bool
pinnedAt:null
pinnedGlobally:bool
pinnedTagId:null
pinnedUntil:null
questionState:object
questionState.resolution:null
replyCount:int
tags:array
title:str
viewCount:int
```

### `GET /api/v1/topics/{tid}`

顶层字段:

```
author.avatar:object
author.displayedBadge:object
author.id:str
author.username:str
author:object
closedAt:null
closedBy:null
collection:null
createdAt:str
editedAt:null
events:array
eventsTruncated:bool
firstPost.author:object
firstPost.children:array
firstPost.cookedHtml:str
firstPost.createdAt:str
firstPost.currentRevision:int
firstPost.deleted:bool
firstPost.editedAt:null
firstPost.id:str
firstPost.likeCount:int
firstPost.markdown:str
firstPost.pinned:bool
firstPost.postNumber:int
firstPost.replyToPostNumber:null
firstPost.topicId:str
firstPost.viewerCapabilities:object
firstPost.viewerState:object
firstPost:object
headerFileId:null
id:str
kind:str
lastActivityAt:str
pinned:bool
pinnedAt:null
pinnedGlobally:bool
pinnedTagId:null
pinnedUntil:null
repliesTruncated:bool
replyCount:int
tags:array
title:str
updatedAt:str
viewCount:int
viewerCapabilities.canBookmark:bool
viewerCapabilities.canClearDuplicate:bool
viewerCapabilities.canClose:bool
viewerCapabilities.canDelete:bool
viewerCapabilities.canEdit:bool
viewerCapabilities.canMarkDuplicate:bool
viewerCapabilities.canPinGlobally:bool
viewerCapabilities.canReopen:bool
viewerCapabilities.canReply:bool
viewerCapabilities.canUnpin:bool
viewerCapabilities.canUnselectAnswer:bool
viewerCapabilities.pinnableTagIds:array
viewerCapabilities:object
viewerState.bookmarkId:null
viewerState:object
```

### `GET /api/v1/topics/{tid}/posts?limit=5`

顶层字段:

```
items:array
pageInfo.hasNextPage:bool
pageInfo.nextCursor:null
pageInfo:object
```

### `GET /api/v1/topics/{tid}/posts?limit=5&sort=latest`

顶层字段:

```
items:array
pageInfo.hasNextPage:bool
pageInfo.nextCursor:null
pageInfo:object
```

### `GET /api/v1/posts/{pid}`

顶层字段:

```
canEdit:bool
contentRole:str
currentRevision:int
editAttemptsRemaining:int
editAttemptsUsed:int
editBlockedReason:str
latestSubmissionId:str
latestSubmissionStatus:str
postId:str
publishedAt:str
request.markdown:str
request.replyToPostNumber:int
request.topicId:str
request:object
topicId:str
```

### `GET /api/v1/tags`

顶层字段:

```
items:array
```

列表项形态 (1 种):

形态 1:

```
description:str
id:str
name:str
slug:str
```

### `GET /api/v1/users/{uid}`

顶层字段:

```
avatar.id:str
avatar.type:str
avatar.url:str
avatar:object
backgroundFileId:str
bio:str
createdAt:str
id:str
level.current:int
level:object
profileVersion:str
region:str
showFollowersList:bool
showFollowingList:bool
stats.followers:int
stats.following:int
stats.replies:int
stats.topics:int
stats:object
username:str
viewerState.canFollow:bool
viewerState.canReport:bool
viewerState.followedBy:bool
viewerState.following:bool
viewerState:object
```

### `GET /api/v1/post-submissions?contentRole=TOPIC_REPLY&status=PENDING`

顶层字段:

```
items:array
pageInfo.hasNextPage:bool
pageInfo.nextCursor:null
pageInfo:object
```

### `GET /api/v1/users/{uid}/posts?role=reply&limit=5`

顶层字段:

```
items:array
pageInfo.hasNextPage:bool
pageInfo.nextCursor:str
pageInfo:object
```

列表项形态 (1 种):

形态 1:

```
bookmarkCount:int
cookedHtml:str
createdAt:str
id:str
likeCount:int
postNumber:int
replyCount:int
topic:object
topic.id:str
topic.title:str
viewCount:int
```

### `GET /api/v1/users/{uid}/followers?limit=5`

顶层字段:

```
items:array
pageInfo.hasNextPage:bool
pageInfo.nextCursor:str
pageInfo:object
```

列表项形态 (1 种):

形态 1:

```
followedAt:str
user:object
user.avatar:object
user.id:str
user.username:str
```

### `GET /api/v1/users/{uid}/following?limit=5`

顶层字段:

```
items:array
pageInfo.hasNextPage:bool
pageInfo.nextCursor:null
pageInfo:object
```

列表项形态 (1 种):

形态 1:

```
followedAt:str
user:object
user.avatar:object
user.id:str
user.username:str
```

### `GET /api/v1/notifications?limit=10`

顶层字段:

```
items:array
pageInfo.hasNextPage:bool
pageInfo.nextCursor:str
pageInfo:object
```

列表项形态 (3 种, 数据形态数与内容无关):

形态 1:

```
createdAt:str
details:object
details.actor:object
details.event:object
details.topic:object
id:str
readAt:null
type:str
```

形态 2:

```
createdAt:str
details:object
details.actor:object
details.excerpt:str
details.post:object
details.topic:object
id:str
readAt:null
type:str
```

形态 3:

```
createdAt:str
details:object
details.actor:object
details.post:object
details.topic:object
id:str
readAt:null
type:str
```

按 `type` 分组 (4 组, 每组字段是该类项**一定有**的字段):

组 `POST_LIKED`:

```
createdAt:str
details:object
details.actor:object
details.post:object
details.topic:object
id:str
readAt:null
type:str
```

组 `REPLY_CREATED`:

```
createdAt:str
details:object
details.actor:object
details.post:object
details.topic:object
id:str
readAt:null
type:str
```

组 `TOPIC_EVENT`:

```
createdAt:str
details:object
details.actor:object
details.event:object
details.topic:object
id:str
readAt:str
type:str
```

组 `USER_MENTIONED`:

```
createdAt:str
details:object
details.actor:object
details.excerpt:str
details.post:object
details.topic:object
id:str
readAt:null
type:str
```

### `GET /api/v1/works?limit=5`

顶层字段:

```
items:array
pageInfo.hasNextPage:bool
pageInfo.nextCursor:str
pageInfo:object
```

列表项形态 (1 种):

形态 1:

```
author:object
author.avatar:object
author.id:str
author.username:str
coinCount:int
commentCount:int
createdAt:str
description:str
favoriteCount:int
forkEnable:bool
id:str
isForked:bool
lastSyncedAt:str
likeCount:int
operation:str
parentUuid:null
preview:str
published:bool
screenshotCoverUrl:str
sourceUserId:str
sourceWorkId:str
type:str
updatedAt:str
viewCount:int
workName:str
```

按 `type` 分组 (3 组, 每组字段是该类项**一定有**的字段):

组 `KITTEN3`:

```
author:object
author.avatar:object
author.id:str
author.username:str
coinCount:int
commentCount:int
createdAt:str
description:str
favoriteCount:int
forkEnable:bool
id:str
isForked:bool
lastSyncedAt:str
likeCount:int
operation:str
parentUuid:null
preview:str
published:bool
screenshotCoverUrl:str
sourceUserId:str
sourceWorkId:str
type:str
updatedAt:str
viewCount:int
workName:str
```

组 `KITTEN4`:

```
author:object
author.avatar:object
author.id:str
author.username:str
coinCount:int
commentCount:int
createdAt:str
description:str
favoriteCount:int
forkEnable:bool
id:str
isForked:bool
lastSyncedAt:str
likeCount:int
operation:str
parentUuid:null
preview:str
published:bool
screenshotCoverUrl:str
sourceUserId:str
sourceWorkId:str
type:str
updatedAt:str
viewCount:int
workName:str
```

组 `NEKO`:

```
author:object
author.avatar:object
author.id:str
author.username:str
coinCount:int
commentCount:int
createdAt:str
description:str
favoriteCount:int
forkEnable:bool
id:str
isForked:bool
lastSyncedAt:str
likeCount:int
operation:str
parentUuid:null
preview:str
published:bool
screenshotCoverUrl:str
sourceUserId:str
sourceWorkId:str
type:str
updatedAt:str
viewCount:int
workName:str
```

### `GET /api/v1/badges`

顶层字段:

```
items:array
```

列表项形态 (1 种):

形态 1:

```
description:str
icon:str
id:str
image:object
image.fileId:str
image.height:int
image.url:str
image.width:int
name:str
slug:str
version:int
```

### `GET /api/v1/emoji-collections`

顶层字段:

```
items:array
page:int
pageSize:int
total:int
```

列表项形态 (1 种):

形态 1:

```
coverUrl:str
createdAt:str
id:str
name:str
position:int
updatedAt:str
version:str
```

### `GET /api/v1/emojis?collectionId={cid}`

顶层字段:

```
items:array
page:int
pageSize:int
total:int
```

列表项形态 (1 种):

形态 1:

```
collectionId:str
createdAt:str
id:str
imageUrl:str
mediaType:str
position:int
state:str
updatedAt:str
version:str
```

### `GET /api/v1/session`

顶层字段:

```
createdAt:str
expiresAt:str
silence:null
user.avatar:object
user.createdAt:str
user.effectivePermissions:array
user.id:str
user.level:object
user.tokenBalance:int
user.username:str
user:object
```

### `GET /api/v1/users/{uid}/topics?limit=5`

顶层字段:

```
items:array
pageInfo.hasNextPage:bool
pageInfo.nextCursor:str
pageInfo:object
```

列表项形态 (1 种):

形态 1:

```
author:object
author.avatar:object
author.id:str
author.username:str
bookmarkCount:int
closedAt:null
createdAt:str
editedAt:null
excerpt:str
id:str
kind:str
lastActivityAt:str
likeCount:int
pinned:bool
pinnedAt:null
pinnedGlobally:bool
pinnedTagId:null
pinnedUntil:null
replyCount:int
tags:array
title:str
viewCount:int
```

### `GET /api/v1/post-submissions/{sid}`

顶层字段:

```
baseRevision:null
commentNumber:null
contentRole:str
createdAt:str
id:str
postId:str
postNumber:int
reasonCode:str
reasonDetails.action:str
reasonDetails.availableAt:str
reasonDetails.evaluatedAt:str
reasonDetails.limit:int
reasonDetails.used:int
reasonDetails.windowSeconds:int
reasonDetails:object
request.kind:str
request.markdown:str
request.tagIds:array
request.title:str
request:object
riskLevel:str
status:str
topicId:str
updatedAt:str
workCommentId:null
workId:null
```

### `GET /api/v1/notifications?limit=50`

顶层字段:

```
items:array
pageInfo.hasNextPage:bool
pageInfo.nextCursor:str
pageInfo:object
```

列表项形态 (4 种, 数据形态数与内容无关):

形态 1:

```
createdAt:str
details:object
details.actor:object
details.event:object
details.topic:object
id:str
readAt:null
type:str
```

形态 2:

```
createdAt:str
details:object
details.actor:object
details.excerpt:str
details.post:object
details.topic:object
id:str
readAt:null
type:str
```

形态 3:

```
createdAt:str
details:object
details.actor:object
details.post:object
details.topic:object
id:str
readAt:null
type:str
```

形态 4:

```
createdAt:str
details:object
details.actor:object
details.userFollowId:str
id:str
readAt:null
type:str
```

按 `type` 分组 (5 组, 每组字段是该类项**一定有**的字段):

组 `POST_LIKED`:

```
createdAt:str
details:object
details.actor:object
details.post:object
details.topic:object
id:str
readAt:null
type:str
```

组 `REPLY_CREATED`:

```
createdAt:str
details:object
details.actor:object
details.post:object
details.topic:object
id:str
readAt:null
type:str
```

组 `TOPIC_EVENT`:

```
createdAt:str
details:object
details.actor:object
details.event:object
details.topic:object
id:str
readAt:str
type:str
```

组 `USER_FOLLOWED`:

```
createdAt:str
details:object
details.actor:object
details.userFollowId:str
id:str
readAt:str
type:str
```

组 `USER_MENTIONED`:

```
createdAt:str
details:object
details.actor:object
details.excerpt:str
details.post:object
details.topic:object
id:str
readAt:null
type:str
```

### `GET /api/v1/topics?limit=5&sort=latest`

顶层字段:

```
items:array
pageInfo.hasNextPage:bool
pageInfo.nextCursor:str
pageInfo:object
```

列表项形态 (2 种, 数据形态数与内容无关):

形态 1:

```
author:object
author.avatar:object
author.id:str
author.username:str
bookmarkCount:int
closedAt:null
createdAt:str
editedAt:null
excerpt:str
id:str
kind:str
lastActivityAt:str
likeCount:int
pinned:bool
pinnedAt:null
pinnedGlobally:bool
pinnedTagId:null
pinnedUntil:null
questionState:object
questionState.resolution:null
replyCount:int
tags:array
title:str
viewCount:int
```

形态 2:

```
author:object
author.avatar:object
author.id:str
author.username:str
bookmarkCount:int
closedAt:null
createdAt:str
editedAt:null
excerpt:str
id:str
kind:str
lastActivityAt:str
likeCount:int
pinned:bool
pinnedAt:null
pinnedGlobally:bool
pinnedTagId:null
pinnedUntil:null
replyCount:int
tags:array
title:str
viewCount:int
```

按 `kind` 分组 (2 组, 每组字段是该类项**一定有**的字段):

组 `DISCUSSION`:

```
author:object
author.avatar:object
author.id:str
author.username:str
bookmarkCount:int
closedAt:null
createdAt:str
editedAt:null
excerpt:str
id:str
kind:str
lastActivityAt:str
likeCount:int
pinned:bool
pinnedAt:str
pinnedGlobally:bool
pinnedTagId:null
pinnedUntil:null
replyCount:int
tags:array
title:str
viewCount:int
```

组 `QUESTION`:

```
author:object
author.avatar:object
author.id:str
author.username:str
bookmarkCount:int
closedAt:null
createdAt:str
editedAt:null
excerpt:str
id:str
kind:str
lastActivityAt:str
likeCount:int
pinned:bool
pinnedAt:null
pinnedGlobally:bool
pinnedTagId:null
pinnedUntil:null
questionState:object
questionState.resolution:null
replyCount:int
tags:array
title:str
viewCount:int
```

### `GET /api/v1/health`

顶层字段:

```
service:str
status:str
timestamp:str
```

### `GET /api/v1/users/{uid}/badges`

顶层字段:

```
items:array
```

列表项形态 (1 种):

形态 1:

```
badge:object
badge.description:str
badge.icon:str
badge.id:str
badge.image:object
badge.name:str
badge.slug:str
badge.version:int
badgeId:str
grantedAt:str
revision:str
userId:str
```

### `GET /api/v1/works/{wid}`

顶层字段:

```
author.avatar:object
author.id:str
author.username:str
author:object
coinCount:int
commentCount:int
createdAt:str
description:str
favoriteCount:int
forkEnable:bool
id:str
isForked:bool
lastSyncedAt:str
likeCount:int
operation:str
parentUuid:null
preview:str
published:bool
screenshotCoverUrl:str
sourceUserId:str
sourceWorkId:str
type:str
updatedAt:str
viewCount:int
workName:str
```

### `GET /api/v1/emoji-collections/{cid}`

顶层字段:

```
coverUrl:str
createdAt:str
id:str
name:str
position:int
updatedAt:str
version:str
```

### `GET /api/v1/topics?limit=5&kind=DISCUSSION`

顶层字段:

```
items:array
pageInfo.hasNextPage:bool
pageInfo.nextCursor:str
pageInfo:object
```

列表项形态 (1 种):

形态 1:

```
author:object
author.avatar:object
author.id:str
author.username:str
bookmarkCount:int
closedAt:null
createdAt:str
editedAt:null
excerpt:str
id:str
kind:str
lastActivityAt:str
likeCount:int
pinned:bool
pinnedAt:null
pinnedGlobally:bool
pinnedTagId:null
pinnedUntil:null
replyCount:int
tags:array
title:str
viewCount:int
```

### `GET /api/v1/files?limit=1`

顶层字段:

```
items:array
pageInfo.hasNextPage:bool
pageInfo.nextCursor:str
pageInfo:object
```

列表项形态 (1 种):

形态 1:

```
id:str
ownership:str
uploadedByUserId:str
```

### `GET /api/v1/files/{fid}`

顶层字段:

```
raw:str
```

### `GET /api/v1/works/{wid}/comments`

顶层字段:

```
items:array
pageInfo.hasNextPage:bool
pageInfo.nextCursor:null
pageInfo:object
```

### `GET /api/v1/users/{uid}/works?limit=1`

顶层字段:

```
items:array
pageInfo.hasNextPage:bool
pageInfo.nextCursor:str
pageInfo:object
```

列表项形态 (1 种):

形态 1:

```
author:object
author.avatar:object
author.id:str
author.username:str
coinCount:int
commentCount:int
createdAt:str
description:str
favoriteCount:int
forkEnable:bool
id:str
isForked:bool
lastSyncedAt:str
likeCount:int
operation:str
parentUuid:null
preview:str
published:bool
screenshotCoverUrl:str
sourceUserId:str
sourceWorkId:str
type:str
updatedAt:str
viewCount:int
workName:str
```

### `GET /api/v1/badges/{bid}`

顶层字段:

```
description:str
icon:str
id:str
image.fileId:str
image.height:int
image.url:str
image.width:int
image:object
name:str
slug:str
version:int
```

### `GET /api/v1/badges?limit=1`

顶层字段:

```
items:array
```

列表项形态 (1 种):

形态 1:

```
description:str
icon:str
id:str
image:object
image.fileId:str
image.height:int
image.url:str
image.width:int
name:str
slug:str
version:int
```


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
