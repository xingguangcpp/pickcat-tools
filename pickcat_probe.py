#!/usr/bin/env python3
"""
pickcat_probe.py — API 巡检器: 持续探测接口表面, 自动记录变化

定位: 手写的接口清单和"坑表"会随服务端更新而失真。这个脚本把它变成**活的**——
每隔一段时间跑一轮, 自动:
  1. 探测全部已知端点 (只读! 绝不写, 不消耗发帖额度)
  2. 记录每个探测的: HTTP 状态 / 耗时 / 错误码 / 响应字段结构 / 条目数
  3. 与上一份快照对比, 识别: 新增端点 / 消失端点 / 状态变化 / 结构变化 / 错误码变化
  4. 写 docs/api-snapshot.json   (当前状态, 机器可读)
     写 docs/API-STATUS.md       (当前状态, 人看的表格, 每次重新生成)
     追加 docs/API-CHANGELOG.md  (变化流水, 只在有变化时追加)

用法:
  python pickcat_probe.py             # 跑一轮
  python pickcat_probe.py --json      # 额外打印机器可读结果
  python pickcat_probe.py --always    # 即使没变化也记一条(记录"服务可用"这件事)
  python pickcat_probe.py --list      # 只看探测清单

设计要点:
  - 服务不可用时 (502/超时) 不空转: 记一条 "服务不可用" 后安静退出, 不重复撞墙。
  - 探测清单只含 GET —— 写接口的"额度/审核/水印"由 pickcat_bot 的 refresh 负责。
  - 400 与 404 要分清: 400 = 端点存在但参数不合法 (可继续深挖), 404 = 端点不存在。
    Zod 的 details 会泄露期望的 schema, 这是重要的探索线索, 所以一并记下来。
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from pickcat_client import (  # noqa: E402
    DEFAULT_BASE, PickcatClient, PickcatError, is_service_down, service_is_down,
    state_file, _atomic_write_json,
)
from pickcat_bot import build_client, load_env  # noqa: E402

SNAPSHOT_PATH = HERE / "docs" / "api-snapshot.json"
STATUS_MD = HERE / "docs" / "API-STATUS.md"
CHANGELOG_MD = HERE / "docs" / "API-CHANGELOG.md"

# 快照格式版本。结构记录粒度变了就 +1 —— 用来让 diff 知道
# "旧基线里的结构记录不完整, 不能拿来和新的比", 免得把
# "我们把记录做细了" 误报成 "服务端改结构了"。
#
# v1: 只有顶层键, 且写死截断 12 个
# v2: 完整顶层键 + 字段路径 + 列表项字段签名(交集, 类型混在里面)
# v3: 完整顶层键 + 字段路径 + 列表项**形态集合**(只看键名) + 独立类型表
#     v2 的签名取交集, 依赖内容 -> feed 换内容就误报;
#     v3 改成与内容无关的形态集合, 类型单独用表记 (null 当"不知道")。
# v4: 在 v3 基础上加 **列表项分组(itemGroups)**: 按判别字段(type/kind)把项
#     分组, 每组记自己的字段集合与类型。
#     v3 的"形态集合求交集"有个致命缺口: 窗口里**多**进来一种种类时,
#     交集会变小 -> 误报"字段消失"。实测 2026-11-03 21:48 踩到:
#     通知列表混进一条 TOPIC_EVENT(它本来就没有 details.post),
#     交集一缩就报成"服务端删了 details.post" —— 纯误报。
#     v4 改成**分组后逐组比, 且只比两边都有的组**:
#     "窗口里恰好装了哪几种"被隔离掉, 真结构变化照样抓得到。
SCHEMA_VERSION = 5

# 判别字段候选(按优先级): 用来把列表项分成"种类"。
# 常见于"不同种类的返回结构不一样"的端点:
#   通知 type=REPLY_CREATED/TOPIC_EVENT/USER_FOLLOWED/USER_MENTIONED/POST_LIKED
#   主题 kind=DISCUSSION/QUESTION
#   作品 type=KITTEN3/KITTEN4/NEKO
DISCRIMINATOR_CANDIDATES = (
    "type", "kind", "status", "role", "category", "contentType", "event", "group",
)

# 判别字段的取值种类数上限。超过这个数说明它多半不是"种类"而是别的枚举。
DISCRIMINATOR_MAX_VALUES = 12

# 顶层键最多记多少个。之前写死 12, 结果 GET /api/v1/topics/{tid} 有 26 个顶层字段,
# 只记了 12 个 —— 另外 14 个字段改了服务端我们完全看不见。
# 现在放宽到 40 (实测最大 26), 超了才截断并标 keysFull=False。
SCHEMA_MAX_KEYS = 40

# 递归收集字段路径时的最大嵌套深度。
# 深度 1 = 只看顶层; 深度 2 = 再进一层对象 (author/stats/firstPost 这层)。
SCHEMA_DEPTH = 2

# --- 2026-11-04: 错误契约维度 -------------------------------------------------
#
# 之前的盲区 (2026-11-04 夜间实测发现):
#   1. **错误响应的信封结构从没被记录过**。巡检只记了错误码 (code) 和 Zod 的
#      details, 没记错误体本身有哪些字段。实测发现服务端错误信封**并不统一**:
#        ROUTE_NOT_FOUND  -> {code, message}                 (没有 statusCode!)
#        INVALID_CURSOR   -> {code, message, statusCode}
#        VALIDATION_FAILED-> {code, message, statusCode, details}
#        401/404 实体错误   -> {code, message, statusCode}
#      服务端哪天给 404 补上 statusCode, 或者给 400 加个 requestId 字段,
#      旧版巡检**完全看不见** —— 文档里的错误处理约定会悄悄过期。
#   2. **限流/缓存相关的响应头从没被记录过**。实测 CORS 声明了
#      `access-control-expose-headers: ETag,Badge-ETag,Emoji-ETag`,
#      但服务端**实际上一个 ETag 都不发**, 也没有任何 X-RateLimit-* 头。
#      哪天服务端开始发 ETag (可以省流量) 或开始限流 (会打断机器人),
#      这对运维是大事, 但旧版巡检看不到。
#
# 这两项都是**与内容无关**的维度 (错误信封/响应头不随数据内容变),
# 所以不会引入内容驱动的假警报。
#
# 记录哪些响应头? 只记"语义上稳定"的那些 —— 不能把 Date / x-request-id /
# Set-Cookie 之类每次都变的头记进来, 否则每轮都报"变化"。
# 而且要分两类 —— 这是踩得最深的坑:
#   **值可比**的 (值由端点策略决定, 每轮都一样):
#     cache-control / vary / access-control-expose-headers
#   **只可比"在不在"**的 (值会随内容或用量变):
#     etag / badge-etag / emoji-etag           -> 值是内容哈希, 内容一变就变
#     x-ratelimit-remaining / x-ratelimit-reset -> 每次请求都在变
#     retry-after                               -> 每次都不同
#     x-ratelimit-limit                         -> 限额本身可能随时间调整
#   如果把后一类也按值比, 那"服务端开始限流"这个**想看到**的变化,
#   会因为"remaining 每次都在变"而变成每轮都报的假警报 ——
#   而假警报比漏报更致命 (告警被无视 = 等于没巡检)。
HEADER_VALUE_COMPARE = (
    "cache-control",
    "vary",
    "access-control-expose-headers",
)
HEADER_PRESENCE_ONLY = (
    "etag",
    "badge-etag",
    "emoji-etag",
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
    "retry-after",
)
STABLE_HEADERS = HEADER_VALUE_COMPARE + HEADER_PRESENCE_ONLY

# "已发布基线" (gitignored): 记录**最后一次成功推送**时的快照。
# 为什么不能直接拿本地 docs/api-snapshot.json 当对比基线?
#   因为它是每轮都被覆盖的。如果某轮 push 失败 (token 过期/网络问题),
#   那一轮的变化会被写进本地文件, 下一轮再对比就"没变化"了 —— 变化被静默吞掉,
#   永远不会再推。拿"已发布基线"对比, 失败的那轮会在下一轮被重新检测到并重试推送。
PUBLISHED_BASELINE = HERE / ".probe_published.json"
# 已写入 CHANGELOG 的那组变化的指纹, 用来避免重试推送时重复追加同一条记录
CHANGELOG_KEY = HERE / ".probe_changelog_key"
# 各端点用哪个字段做分组维度 (gitignored, 跨轮次持久化)。
# 为什么必须持久化: 分组维度一变, 两轮的组就配不上, 结构比对直接失明;
# 而维度如果每轮靠"重新探测"决定, 窗口内容一变维度就可能翻转。
# 所以第一次探测定下来之后就锁住, 除非服务端真的把那个字段删了/改名了。
DISCRIMINATOR_STATE = HERE / ".probe_discriminators.json"

# 探测清单: (说明, 路径, 是否公开接口)
# 公开 = 不带 cookie 也能读; 其余需要登录。
#
# 注意: 第三列"是否公开"是**手写标注**, 只用于和 ANON_PROBES 的**实测**结果对照。
# 真相永远以实测为准 (见 ANON_PROBES 的注释)。
PROBES = [
    ("社区帖子 feed", "/api/v1/topics?limit=5", True),
    ("主题详情", "/api/v1/topics/{tid}", True),
    ("主题下的回复", "/api/v1/topics/{tid}/posts?limit=5", True),
    ("主题回复(按最新排序)", "/api/v1/topics/{tid}/posts?limit=5&sort=latest", True),
    ("单条回复详情", "/api/v1/posts/{pid}", False),
    ("标签列表", "/api/v1/tags", True),
    ("标签带 limit(应被拒: 未知参数)", "/api/v1/tags?limit=20", True),
    ("用户资料(含等级/统计)", "/api/v1/users/{uid}", True),
    ("投稿列表(需 contentRole+status)", "/api/v1/post-submissions?contentRole=TOPIC_REPLY&status=PENDING", False),
    ("用户回复(role=reply)", "/api/v1/users/{uid}/posts?role=reply&limit=5", True),
    ("用户回复(role 非法, 应报错)", "/api/v1/users/{uid}/posts?role=bogus", True),
    ("粉丝列表", "/api/v1/users/{uid}/followers?limit=5", True),
    ("关注列表", "/api/v1/users/{uid}/following?limit=5", True),
    ("通知列表", "/api/v1/notifications?limit=10", False),
    ("作品列表", "/api/v1/works?limit=5", True),
    ("徽章列表", "/api/v1/badges", True),
    ("表情包集列表", "/api/v1/emoji-collections", True),
    ("表情包(需 collectionId)", "/api/v1/emojis?collectionId={cid}", True),
    ("会话(需登录)", "/api/v1/session", False),
    ("我自己的主题(需登录)", "/api/v1/users/{uid}/topics?limit=5", True),
    ("投稿审核状态(需登录)", "/api/v1/post-submissions/{sid}", False),
    ("不存在的端点(应 404)", "/api/v1/__probe_nonexistent__", True),
    ("posts 列表(已知不可用)", "/api/v1/posts?limit=5", True),
    ("非法 cursor", "/api/v1/topics?cursor=abc", True),
    # --- 以下为 2026-11-03 补充: 把"服务端没说但能被问出来"的约束固化成探测项 ---
    ("主题回复 sort 非法(应泄露 sort 枚举)", "/api/v1/topics/{tid}/posts?limit=5&sort=bogus", True),
    ("用户回复加 sort(该端点无 sort 参数)", "/api/v1/users/{uid}/posts?role=reply&sort=bogus", True),
    ("通知 limit 超过上限(应泄露上限值)", "/api/v1/notifications?limit=100", True),
    ("通知 limit 取上限(应 200)", "/api/v1/notifications?limit=50", False),
    ("topics 带 sort(应支持)", "/api/v1/topics?limit=5&sort=latest", True),
    ("粉丝列表带非法 cursor(应被拒)", "/api/v1/users/{uid}/followers?limit=5&cursor=abc", True),
    ("表情包嵌套路径(已知不可用, 须用 collectionId)", "/api/v1/emoji-collections/{cid}/emojis", True),
    # --- 2026-11-03 补充: 端点发现 + 匿名实测校正 ---
    ("服务健康检查", "/api/v1/health", True),
    ("用户徽章", "/api/v1/users/{uid}/badges", True),
    ("作品详情", "/api/v1/works/{wid}", True),
    ("表情包集详情", "/api/v1/emoji-collections/{cid}", True),
    ("topics kind 非法(应泄露 kind 枚举)", "/api/v1/topics?limit=5&kind=bogus", True),
    ("topics kind=DISCUSSION(应 200)", "/api/v1/topics?limit=5&kind=DISCUSSION", True),
    ("works 不支持 kind(应报错)", "/api/v1/works?limit=5&kind=DISCUSSION", True),
    ("用户主题不支持 kind(应报错)", "/api/v1/users/{uid}/topics?limit=5&kind=DISCUSSION", True),
    ("通知 limit 非数字(应报错)", "/api/v1/users/{uid}/followers?limit=abc", True),
    # --- 2026-11-03 14:47 补充: 端点发现 + README 证伪 ---
    # 发现: 之前 README 写"GET /api/v1/files 路由存在但恒 401"是**错的** ——
    #   匿名访问才 401, 带 cookie 是 200 且能列出 13 个文件。
    ("文件列表(需登录)", "/api/v1/files?limit=1", False),
    ("文件详情(公开可读原图)", "/api/v1/files/{fid}", True),
    ("文件不存在(应 404 FILE_NOT_FOUND)", "/api/v1/files/00000000-0000-0000-0000-000000000000", True),
    ("作品评论列表", "/api/v1/works/{wid}/comments", True),
    ("作品评论(按楼号, 需登录)", "/api/v1/works/{wid}/comments/1", False),
    ("作品评论楼号非法(应泄露下限)", "/api/v1/works/{wid}/comments/0", False),
    ("用户作品列表", "/api/v1/users/{uid}/works?limit=1", True),
    ("徽章详情(id 是 slug 非 UUID)", "/api/v1/badges/{bid}", True),
    ("徽章不存在(应 404 BADGE_NOT_FOUND)", "/api/v1/badges/__probe_no_such_badge__", True),
    ("权限列表(需 access.manage)", "/api/v1/permissions", False),
    ("用户等级(需 community.level.manage)", "/api/v1/users/{uid}/level", False),
    ("表情详情(需 community.emoji.manage)", "/api/v1/emojis/{eid}", False),
    # --- 2026-11-03 14:47 补充: 每端点 limit 上限各不相同, 逐个钉死 ---
    ("topics limit 超过上限(应为 50)", "/api/v1/topics?limit=51", True),
    ("works limit 超过上限(应为 50)", "/api/v1/works?limit=51", True),
    ("用户回复 limit 超过上限(应为 50)", "/api/v1/users/{uid}/posts?role=reply&limit=51", True),
    ("主题回复 limit 超过上限(应为 100)", "/api/v1/topics/{tid}/posts?limit=101", True),
    ("文件 limit 超过上限(应为 100)", "/api/v1/files?limit=101", False),
    ("用户作品 limit 超过上限(应为 50)", "/api/v1/users/{uid}/works?limit=51", True),
    # --- 2026-11-03 14:47 补充: feed 的 sort 枚举与主题内回复**不同** ---
    ("topics feed sort 非法(应泄露 relevance|latest)", "/api/v1/topics?limit=5&sort=bogus", True),
    ("topics sort=relevance(浏览模式应被拒)", "/api/v1/topics?limit=5&sort=relevance", True),
    ("作品评论 sort 非法(应泄露 hot|latest|earliest)", "/api/v1/works/{wid}/comments?sort=bogus", True),
    # --- 2026-11-03 14:47 补充: 参数存在性 (未知参数直接 400) ---
    ("表情包集带 limit(该端点无此参数)", "/api/v1/emoji-collections?limit=5", True),
    ("表情包带 limit(该端点无此参数)", "/api/v1/emojis?collectionId={cid}&limit=5", True),
    ("徽章带 limit(该端点接受但不生效)", "/api/v1/badges?limit=1", True),
    # --- 2026-11-04 补充: 错误契约维度 ---
    #
    # 为什么要补这批: 之前的探测项虽然覆盖了一些 404, 但**资源级 404 只覆盖了
    # 文件和徽章两种**。实测服务端对"资源不存在"给的是**按资源类型区分**的错误码,
    # 不是统一的 NOT_FOUND:
    #   topic 不存在        -> TOPIC_NOT_FOUND
    #   user 不存在         -> USER_NOT_FOUND
    #   work 不存在         -> COMMUNITY_WORK_NOT_FOUND   (注意前缀 COMMUNITY_)
    #   work 下的评论不存在 -> WORK_COMMENT_NOT_FOUND
    #   emoji 集不存在     -> EMOJI_COLLECTION_NOT_FOUND
    #   post 不存在         -> POST_NOT_FOUND
    # 这些码是**客户端要按它分流**的 (比如 USER_NOT_FOUND 该提示"用户不存在",
    # 而不是笼统的 404)。之前没监控 = 服务端改码我们不会知道。
    #
    # 全部用固定的全零 UUID -> 一定不存在, 不依赖任何真实内容, 不会随数据变。
    ("主题不存在(应 404 TOPIC_NOT_FOUND)", "/api/v1/topics/00000000-0000-0000-0000-000000000000", True),
    ("用户不存在(应 404 USER_NOT_FOUND)", "/api/v1/users/00000000-0000-0000-0000-000000000000", True),
    ("作品不存在(应 404 COMMUNITY_WORK_NOT_FOUND)", "/api/v1/works/00000000-0000-0000-0000-000000000000", True),
    ("表情包集不存在(应 404 EMOJI_COLLECTION_NOT_FOUND)", "/api/v1/emoji-collections/00000000-0000-0000-0000-000000000000", True),
    ("回复不存在(应 404 POST_NOT_FOUND, 需登录)", "/api/v1/posts/00000000-0000-0000-0000-000000000000", False),
    ("作品评论不存在(应 404 WORK_COMMENT_NOT_FOUND, 需登录)", "/api/v1/works/{fw}/comments/9999", False),
    # 注: 401 有**两个不同的错误码**, 哪个路由配哪个码是固定的:
    #   /api/v1/session -> UNAUTHENTICATED
    #   其余私有端点     -> AUTHENTICATION_REQUIRED
    # 这两个路由的匿名探测已经在跑 (auth 段), anonCode 现在会被比对,
    # 所以不需要额外的探测项 —— 加了反而会**覆盖**同 path 的旧探测项
    # (probes 是按 "GET {path}" 建键的, 同 path 后写的会把先写的顶掉)。
]

# 探测表自身的版本号 (2026-11-04)。
#
# 为什么需要它: 我们扩探测面时, 新加的探测项在快照里表现为"**新增**一个端点"。
# 但那是**我们自己加的探测**, 不是服务端新增了端点 —— 混在一起会让读 CHANGELOG
# 的人误以为服务端变了接口 (假警报)。而"假警报比漏报更致命": 告警一旦不准,
# 就会被无视, 等于没巡检。
#
# 所以: 改了 PROBES 表就把这个号 +1, 快照里记下。diff 时若探测表版本变了,
# 本轮新增/消失的探测项会被标成"巡检器扩面", 与"服务端新增/消失端点"区分开。
# 服务端真的新增端点时 (探测表没动), 仍然照常报"**新增**"。
PROBE_TABLE_REV = 2


# 匿名探测: 用来**实测**每个端点是否公开, 而不是靠 PROBES 里手写的标注。
#
# 为什么必须有这个:
#   上一轮总结过一条硬经验 —— "鉴权属性必须匿名实测, 不能靠看起来像公开标注"。
#   错标注比缺端点更危险: 缺端点会被巡检发现, 错标注不会, 文档会一直骗人。
#   上一轮靠人工挨个试出 4 处标反 —— 但手写标注没有自愈能力:
#   服务端哪天把一个公开端点改成需登录, 带 cookie 的探测照样返回 200, 巡检看不出来。
#
# 这份清单**自动派生** (见 anon_probe_plan), 不手写:
#   手写的清单会漏 —— 加了 PROBES 却忘了加匿名探测, 新端点的鉴权属性就没人验。
#   派生规则: 按"路由"(path 去掉 query) 去重, 每条路由取该路由第一个探测项的
#   path+query 去匿名打一次。带 query 是为了和带 cookie 的那次**同路径同参数**,
#   这样两次结果可直接对照 (见 interpret_auth)。
def anon_probe_plan(probes=None):
    """从 PROBES 派生匿名探测计划: [(说明, path_with_query)] —— 每条路由一条。"""
    seen = set()
    plan = []
    for label, path, _public in (probes if probes is not None else PROBES):
        route = path.split("?", 1)[0]
        if route in seen:
            continue
        seen.add(route)
        plan.append((label, path))
    return plan


def _declared_public(route):
    """取 PROBES 里对该路由的**手写**公开标注 (用于和实测结果对照)。"""
    for _label, path, public in PROBES:
        if path.split("?", 1)[0] == route:
            return public
    return False


def interpret_auth(anon_status, auth_status):
    """由"匿名"与"带 cookie"两次结果, 推断该路由的鉴权属性。

    依据 (2026-11-03 实测):
      - 匿名 200              -> 公开
      - 匿名 401              -> 需登录
      - 匿名 403              -> 需权限 (登录了但权限不足)
      - 匿名 400              -> **公开**: 鉴权中间件在参数校验之前,
        匿名能拿到 400 说明鉴权这一关过了, 只是参数不合法。
      - 匿名 404              -> 路由不存在
      - 其他                  -> 未判定

    注: 匿名 400 判成"公开"依赖"鉴权先于校验"这个顺序。
    这是实测出来的 (私有路由 /api/v1/files 匿名给 401 而非 400), 不是假设。
    """
    if anon_status == 200:
        return "公开"
    if anon_status == 401:
        return "需登录"
    if anon_status == 403:
        return "需权限"
    if anon_status == 400:
        return "公开"
    if anon_status == 404:
        return "不存在"
    return "未判定"



# 自动生成、允许被 git 回滚的三个文件
GENERATED_DOCS = ("docs/api-snapshot.json", "docs/API-STATUS.md", "docs/API-CHANGELOG.md")


def _subst(path, ctx):
    return (path.replace("{tid}", ctx["tid"])
                .replace("{pid}", ctx["pid"])
                .replace("{uid}", ctx["uid"])
                .replace("{cid}", ctx["cid"])
                .replace("{wid}", ctx["wid"])
                .replace("{sid}", ctx["sid"])
                .replace("{fid}", ctx.get("fid", ""))
                .replace("{bid}", ctx.get("bid", ""))
                .replace("{fw}", ctx.get("fw", ""))
                .replace("{eid}", ctx.get("eid", "")))


def build_context(c):
    """拿几个真实 id 当探针参数 (用当前会话/我自己的内容, 不额外写任何东西)。"""
    ctx = {
        "tid": "01a195a5-b39d-7709-ad7b-0494928c6dd3",
        "pid": "01a1972c-0bef-7aa6-858c-d5938427dae4",
        "uid": "01a0d338-72f2-731e-a0c5-10724fc89813",
        "cid": "",
        "wid": "",
        "sid": "01a1956c-c398-7b35-9ff2-626cd135b1dd",
        "fid": "",
        "bid": "badge_bugfinder",
        "eid": "",
        # 固定的作品 id (2026-11-04): 探测"作品评论不存在"要用**固定的**作品,
        # 不能用 {wid} —— {wid} 是"当前第一个作品", 它会随社区新作品而变,
        # 那条探测就会时对时错 (假警报)。
        "fw": "01a1a210-dbc4-7788-af8f-d500d5ab21fb",
    }
    try:
        ses = c.get_session()
        u = (ses.get("user") or {})
        if u.get("id"):
            ctx["uid"] = u["id"]
    except Exception:
        pass
    try:
        cols = c._request("GET", "/api/v1/emoji-collections")
        items = (cols.get("items") if isinstance(cols, dict) else cols) or []
        if items:
            ctx["cid"] = items[0].get("id", "")
    except Exception:
        pass
    try:
        works = c._request("GET", "/api/v1/works?limit=1")
        items = (works.get("items") if isinstance(works, dict) else works) or []
        if items:
            ctx["wid"] = items[0].get("id", "")
    except Exception:
        pass
    # 2026-11-03: 新增探测项需要的真实 id
    try:
        files = c._request("GET", "/api/v1/files?limit=1")
        items = (files.get("items") if isinstance(files, dict) else files) or []
        if items:
            ctx["fid"] = items[0].get("id", "")
    except Exception:
        pass
    try:
        badges = c._request("GET", "/api/v1/badges")
        items = (badges.get("items") if isinstance(badges, dict) else badges) or []
        for b in items:
            if b.get("id"):
                ctx["bid"] = b["id"]
                break
    except Exception:
        pass
    try:
        if ctx["cid"]:
            emos = c._request("GET", "/api/v1/emojis?collectionId=" + ctx["cid"])
            items = (emos.get("items") if isinstance(emos, dict) else emos) or []
            if items:
                ctx["eid"] = items[0].get("id", "")
    except Exception:
        pass
    return ctx


def _type_name(v):
    """给一个值取个稳定的类型名 (用于结构指纹)。"""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "str"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return type(v).__name__


def field_paths(obj, depth=SCHEMA_DEPTH):
    """收集一个对象里的所有字段路径, 形如 "author.username" / "stats.likes:int"。

    为什么记录**路径 + 类型**而不只是顶层键名:
      - 只记顶层键名的话, 服务端把 topics/{tid} 里的 firstPost.cookedHtml 改名,
        巡检完全看不见 —— 而那正是会打断业务的改动。
      - 带上类型能额外发现 "字段还在但类型变了" (比如 id 从 str 变成 int),
        这种改动同样会让调用方炸掉。

    深度受限 (默认 2 层): 再深就进到正文/配置这种大对象里, 噪声大于价值。
    数组不递归 (只记 "[]"), 因为数组元素结构各端点差异大, 且长度会变。
    """
    out = set()

    def walk(o, prefix, level):
        if not isinstance(o, dict):
            return
        for k in sorted(o.keys()):
            v = o[k]
            p = f"{prefix}.{k}" if prefix else k
            out.add(f"{p}:{_type_name(v)}")
            if isinstance(v, dict) and level < depth:
                walk(v, p, level + 1)

    walk(obj, "", 1)
    return sorted(out)


def item_schema(items):
    """从列表项里归纳出"数据形态"。

    这里踩过一个坑, 值得写清楚:
      最初想用"所有项都有的字段交集"当共同签名。但**交集/并集都是内容依赖的** ——
      feed 里换了批帖子, 交集就变, 于是每轮都误报"字段变化"。而且类型信息
      (":null" vs ":str") 一混进来, 内容一变签名就跟着变。
    真正的"结构"是**形态集合**: 把每个项的字段路径(只看名字, 不看类型)归一化去重,
    得到"服务端一共返回过哪几种形状"。这个集合:
      - 与内容无关: feed 换内容但形状没变 -> 集合不变 -> 不报。
      - 服务端给所有项加字段 -> 每个形状都多一个 -> 集合变 -> 报。
      - 服务端引入新形态 (如新的通知类型) -> 集合多一个 -> 报。
      - 服务端删字段 -> 形状少一个 -> 报。
    类型单独用 itemTypes 记 (path -> 类型), 比较时 null 当"不知道"处理,
    这样 int 改 str 能抓到, 而 null 变 str (只是内容变了) 不会误报。
    """
    dicts = [i for i in items if isinstance(i, dict)]
    if not dicts:
        return None, 0, {}

    # 每个项算一份"只看字段名"的形状
    shapes = set()
    for d in dicts:
        paths = sorted({_split_field(f)[0] for f in field_paths(d)})
        shapes.add(tuple(paths))

    # 类型: 逐字段收集所有项里出现过的类型; 只有一种才算确定值,
    # 有多种 (比如 null 和 str 并存) 就记 null = 未知, 比较时不当证据。
    types = {}
    per_path = {}
    for d in dicts:
        for f in field_paths(d):
            p, t = _split_field(f)
            per_path.setdefault(p, set()).add(t)
    for p, ts in per_path.items():
        # 只有一种类型才算确定值; 有多种 (比如 null 和 str 并存) 就记 null = 未知,
        # 比较时不当"类型变化"的证据 (null 只说明这次没值)。
        types[p] = next(iter(ts)) if len(ts) == 1 else "null"

    return sorted(sorted(s) for s in shapes), len(shapes), types


def detect_discriminator(items, prefer=None):
    """找一个能把列表项分成"种类"的字段。返回字段名, 找不到返回 None。

    为什么需要它: 很多列表里**不同种类的项结构不一样** ——
      通知: type=REPLY_CREATED 带 details.post, type=TOPIC_EVENT 只有 details.event
      主题: kind=QUESTION 比 kind=DISCUSSION 多 questionState.*
      作品: type=KITTEN3/KITTEN4/NEKO
    这种端点上, "把窗口里所有形态求交集"这种判据天然会被**窗口内容**带偏:
    窗口里多进来一种种类, 交集就少一批字段 -> 误报"服务端删了字段"。
    实测 2026-11-03 21:48 踩到: 通知列表混进一条 TOPIC_EVENT,
    就报成"消失 details.post" —— 服务端一点没变。

    **判别字段只允许依赖端点 schema, 不许依赖窗口内容** —— 这是本函数最重要的约束。
    踩过的两个坑:
      ① 曾要求"取值种类 >= 2"才算判别字段, 但窗口里恰好只有一种类型时
         (实测 `/users/{uid}/topics` 那轮只有 kind=DISCUSSION),
         判别字段就会从 "kind" 变成 None —— **两轮分组维度不一致**,
         凭空多出"分组维度变化"这种内容驱动的假警报。
      ② 曾要求"至少 2 项"才算, 但窗口里只剩 1 项时同样会翻转维度。
    所以: 这里只看"字段是否存在、值是不是非空字符串", 不看项数、不看取值种类。

    prefer: 上几轮用过的判别字段 (持久化在 .probe_discriminators.json)。
      **优先沿用**, 让分组维度跨轮次保持稳定 ——
      维度一变, 两轮的组就配不上, 比对直接失明。
      prefer 字段在这轮数据里不存在(服务端真的改了)时才回退到自动探测。

    候选只从语义化的白名单里挑 (type/kind/status/...),
    不去碰 id/slug/code 这类标识符字段 ——
    按 id 分组等于每项一组, 两轮对不上任何组, 结构巡检会彻底失明。
    """
    dicts = [i for i in items if isinstance(i, dict)]
    if not dicts:
        return None
    if prefer:
        # prefer 字段只要在**大部分**项上存在就用它;
        # 少数项没有该字段 -> 归到 "" 组, 而不是让整个维度翻转。
        hits = sum(1 for d in dicts
                   if isinstance(d.get(prefer), str) and d.get(prefer))
        if hits >= max(1, len(dicts) // 2):
            return prefer
    for name in DISCRIMINATOR_CANDIDATES:
        ok = True
        for d in dicts:
            v = d.get(name)
            if not isinstance(v, str) or not v:
                ok = False
                break
        if ok:
            return name
    return None


def item_groups(items, prefer=None):
    """把列表项按判别字段分组, 每组记自己的字段集合与类型。

    返回 {"discriminator": 字段名或 None, "groups": {组名: {"fields": [...], "may": [...], "types": {...}}}}

    组内记两套字段集:
      fields = 这一类项**每一条都有**的字段 (组内形态的交集) -> "must"
      may    = 这一类项**至少一条有**的字段 (组内形态的并集) -> "may"
    差分时用 must×may 交叉口径 (见 _fmt_group_delta), 避免组内
    可选字段随内容抖动导致误报。
    """
    dicts = [i for i in items if isinstance(i, dict)]
    if not dicts:
        return None
    disc = detect_discriminator(dicts, prefer)
    buckets = {}
    for d in dicts:
        v = d.get(disc) if disc else ""
        key = v if isinstance(v, str) and v else ""
        buckets.setdefault(key, []).append(d)
    groups = {}
    for key, ds in buckets.items():
        shapes = set()
        per_path = {}
        for d in ds:
            shapes.add(tuple(sorted({_split_field(f)[0] for f in field_paths(d)})))
            for f in field_paths(d):
                p, t = _split_field(f)
                per_path.setdefault(p, set()).add(t)
        sets = [set(s) for s in shapes]
        common = set.intersection(*sets) if sets else set()
        every = set.union(*sets) if sets else set()
        types = {}
        for p, ts in per_path.items():
            types[p] = next(iter(ts)) if len(ts) == 1 else "null"
        groups[key] = {"fields": sorted(common), "may": sorted(every),
                       "types": types}
    return {"discriminator": disc, "groups": groups}


def load_discriminators():
    """读回各端点上次用的分组维度 (键 = 探测项的原始路径)。"""
    d = _load_json(DISCRIMINATOR_STATE)
    return d if isinstance(d, dict) else {}


def save_discriminators(state):
    """原子写回分组维度。"""
    try:
        _atomic_write_json(DISCRIMINATOR_STATE, state)
    except Exception:
        pass


def run_probe(c, label, path, public, prefer_disc=None):
    """跑一个探测, 返回结构化结果 (不抛异常)。"""
    t0 = time.time()
    rec = {"label": label, "path": path, "public": public}
    try:
        d = c._request("GET", path)
        rec["status"] = 200
        rec["latencyMs"] = int((time.time() - t0) * 1000)
        if isinstance(d, dict):
            keys = sorted(d.keys())
            rec["keys"] = keys[:SCHEMA_MAX_KEYS]
            # 记清楚"顶层键记全了没有" —— 否则和旧基线(写死截断 12 个)没法比。
            rec["keysFull"] = len(keys) <= SCHEMA_MAX_KEYS
            items = d.get("items")
            rec["itemCount"] = len(items) if isinstance(items, list) else None
            pi = d.get("pageInfo")
            if isinstance(pi, dict):
                rec["hasNextPage"] = bool(pi.get("hasNextPage"))
            # 结构指纹: 顶层键名不够, 再记一层字段路径。
            rec["fields"] = field_paths(d)
            if isinstance(items, list) and items:
                shapes, variants, types = item_schema(items)
                rec["itemShapes"] = shapes
                rec["itemVariants"] = variants
                rec["itemTypes"] = types
                g = item_groups(items, prefer=prefer_disc)
                if g:
                    rec["itemGroups"] = g
        elif isinstance(d, list):
            rec["keys"] = ["<list>"]
            rec["keysFull"] = True
            rec["itemCount"] = len(d)
            rec["fields"] = []
            shapes, variants, types = item_schema(d)
            if shapes:
                rec["itemShapes"] = shapes
                rec["itemVariants"] = variants
                rec["itemTypes"] = types
                g = item_groups(d, prefer=prefer_disc)
                if g:
                    rec["itemGroups"] = g
        else:
            rec["keys"] = [type(d).__name__]
            rec["keysFull"] = True
            rec["fields"] = []
    except PickcatError as e:
        rec["status"] = e.status
        rec["latencyMs"] = int((time.time() - t0) * 1000)
        rec["code"] = e.code
        body = e.body if isinstance(e.body, dict) else {}
        # 错误信封结构 (2026-11-04): 记错误体**本身有哪些字段**。
        # 之前只记 code/details, 于是服务端给 404 补 statusCode 这类变化看不见。
        if body:
            rec["errKeys"] = sorted(body.keys())
            rec["errKeysFull"] = True
        det = body.get("details")
        if det:
            rec["details"] = json.dumps(det, ensure_ascii=False)[:220]
    except Exception as e:
        rec["status"] = -1
        rec["latencyMs"] = int((time.time() - t0) * 1000)
        rec["code"] = "EXC:" + str(e)[:80]
    # 稳定响应头 (2026-11-04): 值可比的头记值; 只可比"在不在"的头只记存在性。
    # 分开的原因见 HEADER_PRESENCE_ONLY 的注释 —— 把 remaining 这类每次都变的
    # 值拿来比, "服务端开始限流"这个想看的变化会被淹没在假警报里。
    hdrs = getattr(c, "_last_headers", None) or {}
    if hdrs:
        hval = {k: hdrs[k] for k in HEADER_VALUE_COMPARE if k in hdrs}
        hpres = sorted(k for k in HEADER_PRESENCE_ONLY if k in hdrs)
        if hval:
            rec["headerValues"] = hval
        if hpres:
            rec["headerPresent"] = hpres
    return rec


def _split_field(f):
    """把 "author.name:str" 拆成 ("author.name", "str")。"""
    if ":" in f:
        path, ty = f.rsplit(":", 1)
        return path, ty
    return f, None


def _fmt_field_delta(old, new, limit=8):
    """把两份字段指纹的差异说成人话: 加了什么 / 丢了什么 / 类型变了。

    **null 必须当成"不知道"**, 这是本函数最容易踩的坑:
      实测有大量字段平时就是 null (closedAt / editedAt / pinnedUntil /
      replyToPostNumber / bookmarkId ...)。内容一变 (主题被关、被编辑、被置顶),
      这些字段就从 null 变成具体值 —— 那是**内容变化**, 不是服务端改了结构。
      如果直接比类型, 每轮都可能误报"字段路径变化", 巡检就成了狼来了。
    所以规则:
      - 字段名新增/消失 -> 真结构变化, 必报。
      - 类型变化 -> 只有**两边都不是 null** 才报 (null 只说明"这次没值",
        不能推出类型变了)。这仍然能抓住 int 改 str 这种真改动。
    """
    o = {}
    for f in old or []:
        p, t = _split_field(f)
        o[p] = t
    n = {}
    for f in new or []:
        p, t = _split_field(f)
        n[p] = t
    added = sorted(set(n) - set(o))
    removed = sorted(set(o) - set(n))
    retyped = []
    for p in sorted(set(o) & set(n)):
        ot, nt = o[p], n[p]
        if ot == nt:
            continue
        if ot == "null" or nt == "null":
            # 一边是 null -> 只说明这次没值, 不构成类型变化的证据
            continue
        retyped.append(f"{p}: {ot}→{nt}")
    parts = []
    if added:
        parts.append("新增 " + ", ".join(f"`{a}`" for a in added[:limit])
                     + (f" 等 {len(added)} 个" if len(added) > limit else ""))
    if removed:
        parts.append("消失 " + ", ".join(f"`{a}`" for a in removed[:limit])
                     + (f" 等 {len(removed)} 个" if len(removed) > limit else ""))
    if retyped:
        parts.append("类型变化 " + ", ".join(f"`{a}`" for a in retyped[:limit])
                     + (f" 等 {len(retyped)} 个" if len(retyped) > limit else ""))
    return "; ".join(parts)
def _is_struct_change(line):
    """判断一条变化描述是不是"结构类" (与快照记录粒度有关, 不是服务端行为变化)。

    结构类: 响应结构变化 / 字段路径变化 / 列表项形态变化 / 列表项字段类型变化 /
            列表项分组结构变化 / 列表项分组字段类型变化 / 列表项分组维度变化。
    这些在"快照格式升级"那一轮必须静音 —— 否则会把"我们换了把更细的尺子"
    报成"服务端改了 14 个字段"。
    """
    if line.startswith("📐"):
        return False   # 这条本身就是"格式升级"说明, 不是结构差异
    return ("响应结构变化" in line or "字段路径变化" in line
            or "列表项形态变化" in line or "列表项字段类型变化" in line
            or "列表项分组结构变化" in line
            or "列表项分组字段类型变化" in line
            or "列表项分组维度变化" in line)


def _universal_fields(shapes):
    """所有形态**都**有的字段名集合 (交集)。"""
    sets = [set(s) for s in (shapes or [])]
    if not sets:
        return set()
    return set.intersection(*sets)


def _fmt_group_delta(old_g, new_g, limit=8):
    """把两份"分组结构"的差异说成人话。

    口径: **只比两边都有的组**。
      组出现/消失 = 窗口内容变了 (这轮恰好装进了/顶出去了某一种通知),
      不是服务端改了结构 —— 实测 2026-11-03 21:48 就是这么误报的:
      通知列表混进一条 TOPIC_EVENT (它本来就没 details.post),
      全局形态交集一缩 -> 报成"服务端删了 details.post"。
      分组后, TOPIC_EVENT 是个独立组, 根本不影响 REPLY_CREATED 组 -> 不报。
      代价: 服务端**新增一种通知类型**这种变化会被漏掉 (组只在一边出现)。
      宁可漏报, 不要天天误报 —— 那些组仍完整记在快照与 API-STATUS.md 里。

    组内比较用 **must / may** 两套字段集 (和全局形态差分同一套逻辑):
      must = 这一类项**每一条都有**的字段 (组内形态的交集)
      may  = 这一类项**至少一条有**的字段 (组内形态的并集)
      - "消失": 旧 must 有, 新 may 都没有 -> 这一类里这个字段彻底没了
      - "新增": 新 must 有, 旧 may 都没有 -> 这一类里这个字段是新加的
    为什么不用 may 比 may: 组内**可选**字段 (只部分项有) 会随内容抖动,
      用 may 会误报; 而用 must 比 must 会漏掉"只加在部分项上"的变化。
      must×may 这个交叉口径两边都不误报, 代价是漏报一部分边界情况。
    """
    og = (old_g or {}).get("groups") or {}
    ng = (new_g or {}).get("groups") or {}
    disc = (old_g or {}).get("discriminator") or ""
    prefix = f"{disc}=" if disc else ""
    parts = []
    for key in sorted(set(og) & set(ng)):
        o_g, n_g = og[key] or {}, ng[key] or {}
        o_must = set(o_g.get("fields") or [])
        o_may = set(o_g.get("may") or o_g.get("fields") or [])
        n_must = set(n_g.get("fields") or [])
        n_may = set(n_g.get("may") or n_g.get("fields") or [])
        added = sorted(n_must - o_may)
        removed = sorted(o_must - n_may)
        if not added and not removed:
            continue
        seg = []
        if added:
            seg.append("新增 " + ", ".join(f"`{a}`" for a in added[:limit])
                       + (f" 等 {len(added)} 个" if len(added) > limit else ""))
        if removed:
            seg.append("消失 " + ", ".join(f"`{r}`" for r in removed[:limit])
                       + (f" 等 {len(removed)} 个" if len(removed) > limit else ""))
        parts.append(f"[{prefix}{key}] " + "; ".join(seg))
    return "; ".join(parts)


def _fmt_group_type_delta(old_g, new_g, limit=6):
    """分组内的字段类型变化 (null = "不知道", 不当证据)。"""
    og = (old_g or {}).get("groups") or {}
    ng = (new_g or {}).get("groups") or {}
    disc = (old_g or {}).get("discriminator") or ""
    prefix = f"{disc}=" if disc else ""
    parts = []
    for key in sorted(set(og) & set(ng)):
        o_g, n_g = og[key] or {}, ng[key] or {}
        o_may = set(o_g.get("may") or o_g.get("fields") or [])
        n_may = set(n_g.get("may") or n_g.get("fields") or [])
        only = o_may & n_may
        d = _fmt_type_delta(o_g.get("types") or {}, n_g.get("types") or {}, only=only)
        if d:
            parts.append(f"[{prefix}{key}] {d}")
    return "; ".join(parts)


def _fmt_shape_delta(old, new, limit=8):
    """把两份"形态集合"的差异说成人话。

    形态 = 一个项的字段名集合 (只看名字, 不看类型)。

    **只比"所有形态都有的字段"** (交集), 这是防假警报的关键:
      实测踩过的坑: `topics?limit=5&sort=latest` 是**滑动窗口** (按最新排序),
      窗口里本来有一个 QUESTION 类主题 (它比普通主题多 `questionState.*` 字段)。
      那个主题被新主题顶出窗口后, 集合里少了这一种形态 —— 但服务端**一点没变**,
      变的只是"这轮恰好装进了哪几个主题"。
      如果按形态集合直接对比, 就会误报"服务端删了 questionState" —— 狼来了。
      改成只看交集后: 窗口内容怎么变都不影响结论, 只有**所有形态都变了**才报。

    代价: "某一类形态特有"的字段 (比如 QUESTION 独有的 questionState) 不会被自动告警。
    但那些字段仍完整记在快照和 API-STATUS.md 里 (随时能查), 只是不拿去报警 ——
    宁可漏报, 不要天天误报。
    """
    o_u = _universal_fields(old)
    n_u = _universal_fields(new)
    if not o_u and not n_u:
        return ""
    added = sorted(n_u - o_u)
    removed = sorted(o_u - n_u)
    parts = []
    if added:
        parts.append("新增 " + ", ".join(f"`{a}`" for a in added[:limit])
                     + (f" 等 {len(added)} 个" if len(added) > limit else ""))
    if removed:
        parts.append("消失 " + ", ".join(f"`{a}`" for a in removed[:limit])
                     + (f" 等 {len(removed)} 个" if len(removed) > limit else ""))
    return "; ".join(parts)


def _fmt_type_delta(old, new, limit=6, only=None):
    """字段类型变化。null = "不知道", 所以 null↔具体值不报, int→str 才报。

    only: 只比这些字段 (通常是"所有形态都有的"那批)。
      为什么也要限定: 类型是按**所有项**汇总的, 某个只属于一种形态的字段
      (如 QUESTION 独有的 questionState) 在窗口内容一变就可能从"没出现"变成
      "出现" -> 类型表跳动 -> 误报。限定在通用字段上就跟形态差分保持一致了。
    """
    o = old or {}
    n = new or {}
    changed = []
    for p in sorted(set(o) | set(n)):
        if only is not None and p not in only:
            continue
        ot, nt = o.get(p), n.get(p)
        if ot == nt:
            continue
        if ot in (None, "null") or nt in (None, "null"):
            continue
        changed.append(f"{p}: {ot}→{nt}")
    if not changed:
        return ""
    return "; ".join(changed[:limit]) + (f" 等 {len(changed)} 个" if len(changed) > limit else "")


def diff_snapshots(old, new):
    """对比两份快照, 返回变化列表。

    结构比对的两个前提 (缺一个就会出假警报):
      1. **同格式可比**: 旧基线的结构记录可能是"写死截断 12 个顶层键"的粗粒度,
         新记录是完整的 —— 拿粗粒度去比细粒度, 会把"我们把记录做细了"
         误报成"服务端改了 14 个字段"。所以只有两边都记全了才直接比,
         否则只比公共前缀。
      2. **字段路径可比**: 只有两边都记了字段路径 (fields) 才比字段路径。
         旧基线压根没记, 那就跳过, 留到下一轮基线才有得比。
    """
    changes = []
    op = old.get("probes") or {}
    np_ = new.get("probes") or {}
    # 探测表版本变了 = 这轮新增/消失的探测项是**我们自己加的**, 不是服务端变的。
    # 标注清楚, 别让读 CHANGELOG 的人误以为服务端改了接口。
    table_changed = (old.get("probeTableRev") != new.get("probeTableRev"))
    for key, rec in np_.items():
        prev = op.get(key)
        if prev is None:
            if table_changed:
                changes.append(
                    f"🔧 **巡检器新增探测项** `{key}` — {rec.get('label')} "
                    f"(HTTP {rec.get('status')})")
            else:
                changes.append(
                    f"**新增** `{key}` — {rec.get('label')} (HTTP {rec.get('status')})")
            continue
        if prev.get("status") != rec.get("status"):
            changes.append(
                f"`{key}` 状态 {prev.get('status')} → **{rec.get('status')}**"
                f" ({rec.get('code') or ''})")
        if prev.get("code") != rec.get("code") and rec.get("code"):
            changes.append(f"`{key}` 错误码 {prev.get('code')} → **{rec.get('code')}**")
        # 顶层键: 只有两边都是"记全了"的才直接比。
        # 一边截断一边完整 -> 只比公共前缀, 不报"服务端变了"。
        pk, nk = prev.get("keys"), rec.get("keys")
        if pk is not None and nk is not None:
            if prev.get("keysFull") and rec.get("keysFull"):
                if pk != nk:
                    changes.append(
                        f"`{key}` 响应结构变化: {pk} → **{nk}**")
            else:
                m = min(len(pk), len(nk))
                if pk[:m] != nk[:m]:
                    changes.append(
                        f"`{key}` 响应结构变化: {pk[:m]} → **{nk[:m]}**")
        # 字段路径 (第二层): 只有两边都记了才比。
        # 注意: 顶层响应里的**嵌套对象**也可能只属于某一类内容
        # (比如主题详情里的 firstPost 只有帖子才有, 某些端点可能压根没有)。
        # 所以比的时候只看"两边都稳定存在"的字段, 否则窗口内容一变就误报。
        pf, nf = prev.get("fields"), rec.get("fields")
        if pf is not None and nf is not None and pf != nf:
            delta = _fmt_field_delta(pf, nf)
            if delta:
                changes.append(f"`{key}` 字段路径变化: {delta}")
        # 列表项**分组结构** (v4): 按判别字段分组后逐组比, 且只比两边都有的组。
        # 为什么不用全局形态交集 (v3 的做法): 全局交集会被"窗口里多了一种种类"带偏 ——
        # 实测 2026-11-03 21:48 误报"消失 details.post", 服务端一点没变,
        # 只是通知列表混进了一条本来就没有 details.post 的 TOPIC_EVENT。
        # 分组后这种内容抖动被隔离在组内, 不会再污染别的组。
        og, ng = prev.get("itemGroups"), rec.get("itemGroups")
        if og is not None and ng is not None:
            od, nd = og.get("discriminator"), ng.get("discriminator")
            if od == nd:
                delta = _fmt_group_delta(og, ng)
                if delta:
                    changes.append(f"`{key}` 列表项分组结构变化: {delta}")
                tdelta = _fmt_group_type_delta(og, ng)
                if tdelta:
                    changes.append(f"`{key}` 列表项分组字段类型变化: {tdelta}")
            else:
                # 分组维度变了 -> 两轮的组配不上, 无法逐组比。
                # 这既可能是服务端真把字段改名了, 也可能只是这轮没探到那个字段。
                # 保守起见: **不报**维度变化 (内容驱动的假警报代价更高),
                # 改用全局形态口径兜底, 至少还能看出字段整体变了没有。
                ps2, ns2 = prev.get("itemShapes"), rec.get("itemShapes")
                if ps2 is not None and ns2 is not None and ps2 != ns2:
                    d2 = _fmt_shape_delta(ps2, ns2)
                    if d2:
                        changes.append(f"`{key}` 列表项形态变化: {d2}")
        else:
            # 没有分组信息 (老基线) -> 退回 v3 的全局形态交集口径
            ps, ns = prev.get("itemShapes"), rec.get("itemShapes")
            if ps is not None and ns is not None and ps != ns:
                delta = _fmt_shape_delta(ps, ns)
                if delta:
                    changes.append(f"`{key}` 列表项形态变化: {delta}")
            pt, nt = prev.get("itemTypes"), rec.get("itemTypes")
            if pt is not None and nt is not None and pt != nt:
                only = None
                if ps is not None and ns is not None:
                    only = _universal_fields(ps) & _universal_fields(ns)
                delta = _fmt_type_delta(pt, nt, only=only)
                if delta:
                    changes.append(f"`{key}` 列表项字段类型变化: {delta}")
        # 注: 形态**个数**的变化不再单独报一条 —— 它与上面的形态集合差分是
        # 同一件事 (集合变了个数必然变), 单独报会变成重复噪音。
        # itemVariants 仍然记在快照里, 供人看表格时参考。
        if prev.get("itemCount") is not None and rec.get("itemCount") is not None \
                and prev.get("itemCount") != rec.get("itemCount"):
            changes.append(
                f"`{key}` 条目数 {prev.get('itemCount')} → {rec.get('itemCount')}")
        # 错误详情变化 (Zod details 会泄露 schema, 变了就值得记)
        if prev.get("details") != rec.get("details") \
                and (rec.get("details") or prev.get("details")):
            changes.append(
                f"`{key}` 错误详情变化: {str(prev.get('details'))[:110]} → "
                f"**{str(rec.get('details'))[:110]}**")
        # 错误信封结构变化 (2026-11-04): 错误体**有哪些字段**变了。
        # 实测服务端错误信封并不统一 (ROUTE_NOT_FOUND 连 statusCode 都没有),
        # 服务端哪天补上/删掉一个字段, 客户端的错误处理约定就得跟着改。
        # 只在两边都记了才比 (旧基线没这个字段 -> 跳过, 留到下一轮基线才有得比)。
        pek, rek = prev.get("errKeys"), rec.get("errKeys")
        if pek is not None and rek is not None and pek != rek:
            changes.append(f"`{key}` 错误信封结构变化: {pek} → **{rek}**")
        # 响应头: 值可比的那几个比值, 只比存在性的那几个比"在不在"。
        # 分开是因为 etag 的值是内容哈希、x-ratelimit-remaining 每次请求都在变 ——
        # 按值比会把"服务端开始限流"这个想看的变化淹在假警报里。
        phv, nhv = prev.get("headerValues"), rec.get("headerValues")
        if phv is not None and nhv is not None and phv != nhv:
            for hk in sorted(set(phv) | set(nhv)):
                if phv.get(hk) != nhv.get(hk):
                    changes.append(
                        f"`{key}` 响应头 `{hk}` 变化: "
                        f"{phv.get(hk)} → **{nhv.get(hk)}**")
        php, nhp = prev.get("headerPresent"), rec.get("headerPresent")
        if php is not None and nhp is not None and php != nhp:
            added = [h for h in nhp if h not in php]
            gone = [h for h in php if h not in nhp]
            bits = []
            if added:
                bits.append("新增 " + ", ".join(added))
            if gone:
                bits.append("消失 " + ", ".join(gone))
            changes.append(f"`{key}` 响应头出现/消失: **{'; '.join(bits)}**")
    for key, rec in op.items():
        if key not in np_:
            changes.append(f"**消失** `{key}` (原 HTTP {rec.get('status')})")

    # --- 快照格式升级: 这不是"服务端变了", 而是我们把尺子换精细了 ---
    # 旧基线是粗粒度 (顶层键截断 12 个, 且没记字段路径)。拿它跟新基线逐项比
    # 会把"我们记录得更细了"误报成"服务端改了 N 个字段" —— 那是假警报,
    # 而且会让 CHANGELOG 被自己的工具改动淹没。
    # 所以: 格式版本不一致时, 明确记一条"基线升级", 并且**跳过结构类比对**
    # (状态/错误码/新增/消失/鉴权这些照常比, 那些与记录粒度无关)。
    ov = old.get("schemaVersion")
    nv = new.get("schemaVersion")
    fmt_changed = (ov != nv)
    if fmt_changed:
        changes = [c for c in changes if not _is_struct_change(c)]
        changes.append(
            f"📐 **快照格式升级** {ov or 'v1(无版本)'} → v{nv}: "
            f"结构记录改为完整顶层键 + 字段路径 + 列表项形态集合 + 分组结构 "
            f"(本轮不逐项报结构差异, 新基线从下一轮开始生效)")

    # 鉴权属性变化: 这是最重要的一类变化 —— 端点还在, 但"谁能看"变了。
    oa = old.get("auth") or {}
    na = new.get("auth") or {}
    for route, rec in na.items():
        prev = oa.get(route)
        if prev is None:
            # 新路由: 上面的"新增"已经报过一次, 但**鉴权矛盾也得报** ——
            # 新加的探测项把公开端点标成需登录 (或反之) 同样是错标注,
            # 而错标注比缺端点更危险 (文档会一直骗人)。
            if rec.get("mismatch"):
                changes.append(
                    f"⚠️ `{route}` 探针表标注与实测不符: 标注 {rec.get('declared')}, "
                    f"实测 **{rec.get('mode')}** (匿名 {rec.get('anonStatus')})")
            continue
        if prev.get("mode") != rec.get("mode"):
            changes.append(
                f"⚠️ `{route}` 鉴权属性变化: {prev.get('mode')} → **{rec.get('mode')}**")
        if prev.get("declared") != rec.get("declared"):
            changes.append(
                f"`{route}` 探针表标注变化: {prev.get('declared')} → "
                f"**{rec.get('declared')}**")
        # 标注与实测的矛盾: 只在"翻转"时报一次, 不每轮重复报同一条
        if bool(prev.get("mismatch")) != bool(rec.get("mismatch")):
            if rec.get("mismatch"):
                changes.append(
                    f"⚠️ `{route}` 探针表标注与实测不符: 标注 {rec.get('declared')}, "
                    f"实测 **{rec.get('mode')}** (匿名 {rec.get('anonStatus')})")
            else:
                changes.append(
                    f"`{route}` 标注与实测现已一致 ({rec.get('mode')})")
        # 匿名错误码变化 (2026-11-04): anonCode 一直被记着, 但**从没被比对过**。
        # 实测发现服务端 401 有两个不同的码: /api/v1/session 给 UNAUTHENTICATED,
        # 其余私有端点给 AUTHENTICATION_REQUIRED。这两个码哪个配哪个路由,
        # 客户端的错误处理要按它分流 —— 服务端换码而巡检不报, 就会悄悄错配。
        pac, nac = prev.get("anonCode"), rec.get("anonCode")
        if pac != nac and (pac or nac):
            changes.append(
                f"`{route}` 匿名错误码 {pac or '(无)'} → **{nac or '(无)'}**")
    for route, rec in oa.items():
        if route not in na:
            changes.append(f"**消失(路由)** `{route}` (原 {rec.get('mode')})")
    return changes


def _load_json(path):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def load_baseline():
    """取"上一份已发布的快照"作为对比基线。

    优先用 PUBLISHED_BASELINE (上次成功推送时的快照); 没有就用仓库里已提交的
    docs/api-snapshot.json (首次运行 / 手动删过基线文件时的兜底)。
    """
    for p in (PUBLISHED_BASELINE, SNAPSHOT_PATH):
        d = _load_json(p)
        if isinstance(d, dict) and d.get("probes"):
            return d
    return {}


def save_published_baseline(snap):
    """推送成功后调用: 把当前快照记为"已发布基线"。"""
    try:
        _atomic_write_json(PUBLISHED_BASELINE, snap)
    except Exception:
        pass


def changes_fingerprint(changes):
    """变化的指纹, 用来判断"这组变化是否已经写进 CHANGELOG 了"。"""
    return hashlib.sha1(
        "\n".join(sorted(changes)).encode("utf-8")).hexdigest()


def changelog_already_has(changes):
    """这组变化是否已经写进 CHANGELOG (避免重试推送时重复追加)。"""
    fp = changes_fingerprint(changes)
    try:
        if CHANGELOG_KEY.exists() and \
                CHANGELOG_KEY.read_text(encoding="utf-8").strip() == fp:
            return True
    except Exception:
        pass
    return False


def mark_changelog_written(changes):
    try:
        CHANGELOG_KEY.write_text(changes_fingerprint(changes), encoding="utf-8")
    except Exception:
        pass


def write_status_md(snap):
    """生成 docs/API-STATUS.md (每次重新生成, 反映当前真实状态)。"""
    auth_map = snap.get("auth") or {}
    lines = [
        "# API 实时状态 (自动生成)",
        "",
        f"> 由 `pickcat_probe.py` 于 **{snap.get('generatedAt')}** 自动生成, 不要手改。",
        "> 手写补充请放 README 的「已知 API 坑」一节; 本文件只反映**当前探测到的真实状态**。",
        "",
        f"- 服务可用: **{'是' if snap.get('serviceUp') else '否'}**",
        f"- 探测端点数: {len(snap.get('probes') or {})}",
        f"- 已实测鉴权的路由数: {len(auth_map)}",
        f"- 快照格式版本: v{snap.get('schemaVersion')}",
        "",
        "| 端点 | 说明 | 鉴权 | 状态 | 耗时 | 条目 | 备注 |",
        "|---|---|---|---|---|---|---|",
    ]
    for key, rec in (snap.get("probes") or {}).items():
        route = key.split("?", 1)[0].replace("GET ", "")
        a = auth_map.get(route) or {}
        mode = a.get("mode") or ("公开" if rec.get("public") else "需登录")
        st = rec.get("status")
        mark = "✅" if st == 200 else ("⚠️" if st == 400 else "❌")
        note = rec.get("code") or ""
        if rec.get("details"):
            note += " " + rec["details"][:80]
        lines.append(
            f"| `{key}` | {rec.get('label')} | {mode} | {mark} {st} | "
            f"{rec.get('latencyMs')}ms | {rec.get('itemCount', '-')} | {note} |"
        )

    # --- 响应结构明细: 把"字段级"的真实结构也摊开给人看 ---
    #
    # 为什么单开一节: 上面的表只说"这个端点活着", 但接口清单真正要回答的问题是
    # "它返回哪些字段"。手写这种清单必然过期 —— 现在它是从实测响应自动生成的。
    struct_lines = ["", "## 响应结构 (实测字段指纹)", "",
                    "> 自动生成: 下面是各端点**实际返回**的字段路径 + 类型。",
                    "> 服务端改字段名/改类型/删字段, 巡检会立刻报出来并记进 CHANGELOG。",
                    "> 深度: 顶层 + 再进一层对象 (如 `author.*` / `stats.*` / `firstPost.*`)。",
                    ""]
    any_struct = False
    for key, rec in (snap.get("probes") or {}).items():
        if rec.get("status") != 200:
            continue
        fields = rec.get("fields") or []
        shapes = rec.get("itemShapes") or []
        types = rec.get("itemTypes") or {}
        if not fields and not shapes:
            continue
        any_struct = True
        struct_lines.append(f"### `{key}`")
        struct_lines.append("")
        if fields:
            struct_lines.append("顶层字段:")
            struct_lines.append("")
            struct_lines.append("```")
            for f in fields:
                struct_lines.append(f)
            struct_lines.append("```")
        if shapes:
            v = rec.get("itemVariants")
            struct_lines.append("")
            struct_lines.append(
                f"列表项形态 ({len(shapes)} 种"
                + (", 数据形态数与内容无关" if len(shapes) > 1 else "") + "):")
            for i, shape in enumerate(shapes, 1):
                struct_lines.append("")
                struct_lines.append(f"形态 {i}:")
                struct_lines.append("")
                struct_lines.append("```")
                for f in shape:
                    # 类型能从 itemTypes 里补上就补上 (null = 该字段本次没值/类型未定)
                    ty = types.get(f)
                    struct_lines.append(f"{f}:{ty}" if ty else f)
                struct_lines.append("```")
        grp = rec.get("itemGroups") or {}
        groups = grp.get("groups") or {}
        if len(groups) > 1:
            disc = grp.get("discriminator")
            struct_lines.append("")
            struct_lines.append(
                f"按 `{disc}` 分组 ({len(groups)} 组, 每组字段是该类项**一定有**的字段):")
            for gkey in sorted(groups):
                g = groups[gkey] or {}
                struct_lines.append("")
                struct_lines.append(f"组 `{gkey}`:")
                struct_lines.append("")
                struct_lines.append("```")
                for f in g.get("fields") or []:
                    ty = (g.get("types") or {}).get(f)
                    struct_lines.append(f"{f}:{ty}" if ty else f)
                struct_lines.append("```")
        struct_lines.append("")
    if any_struct:
        lines.extend(struct_lines)
    lines += [
        "",
        "## 怎么读这张表",
        "",
        "- ✅ 200 = 端点可用",
        "- ⚠️ 400 = **端点存在但参数不合法** (可继续深挖, `details` 里会泄露期望的 schema)",
        "- ❌ 404 = 端点不存在",
        "",
        "### 鉴权列是**实测**的, 不是手写的",
        "",
        "每条路由都会**不带 cookie** 再打一次, 据此判定它到底公开还是要登录:",
        "",
        "| 匿名结果 | 判定 | 含义 |",
        "|---|---|---|",
        "| 200 | 公开 | 不用登录就能读 |",
        "| 401 | 需登录 | 路由存在, 差一个 cookie |",
        "| 403 | 需权限 | 登录了但权限不够 (如社区管理) |",
        "| 400 | 公开 | 鉴权这关过了, 只是参数不合法 |",
        "| 404 | 不存在 | 路由本身没有 |",
        "",
        "服务端哪天把一个公开端点改成需登录, 这里会立刻变成 ⚠️ 并记进 CHANGELOG。",
        "",
        "### 400 的价值",
        "",
        "服务端用 Zod 校验 query 参数, 且**未知参数直接拒绝**。所以:",
        "",
        "```",
        "GET /api/v1/users/{uid}/posts?role=bogus",
        '-> 400 {\"details\":{\"role\":[\"Invalid input: expected \\\\\"reply\\\\\"\"]}}',
        "```",
        "",
        "这条错误信息等于**接口的 schema 说明书** —— 枚举值、必填参数、类型都在里面。",
        "反过来, 任何 query 参数一旦报 400 且 details 没提到它, 说明**这个参数名不存在**。",
        "",
    ]
    STATUS_MD.write_text("\n".join(lines), encoding="utf-8")


def append_changelog(changes, snap):
    if not changes:
        return False
    # 同一组变化只写一次 (重试推送时别把 CHANGELOG 灌成重复段)
    if changelog_already_has(changes):
        return False
    CHANGELOG_MD.parent.mkdir(parents=True, exist_ok=True)
    if not CHANGELOG_MD.exists():
        CHANGELOG_MD.write_text(
            "# API 变更日志 (自动生成)\n\n"
            "> 由 `pickcat_probe.py` 维护。每次探测发现差异就追加一节。\n\n",
            encoding="utf-8")
    body = [f"\n## {snap.get('generatedAt')}\n"]
    for ch in changes:
        body.append(f"- {ch}")
    with CHANGELOG_MD.open("a", encoding="utf-8") as f:
        f.write("\n".join(body) + "\n")
    mark_changelog_written(changes)
    return True


def _env_token():
    """从 .env 里读 GITHUB_TOKEN (只用于自动推送, 绝不打印)。"""
    f = HERE / ".env"
    if not f.exists():
        return None
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("GITHUB_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _git(*args):
    """在仓库目录里跑 git, 拿输出做判断。"""
    return subprocess.run(["git", *args], cwd=str(HERE),
                          capture_output=True, text=True)


def _autopush_enabled():
    """开关写在 .env 里 (PICKCAT_PROBE_AUTOPUSH=1); 进程环境变量可以覆盖。"""
    ap = os.environ.get("PICKCAT_PROBE_AUTOPUSH")
    ef = HERE / ".env"
    if ap is None and ef.exists():
        for line in ef.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("PICKCAT_PROBE_AUTOPUSH="):
                ap = line.split("=", 1)[1].strip().strip('"').strip("'")
    return ap == "1"


def _heartbeat_hours():
    """心跳间隔 (小时): 无语义变化时, 最多隔这么久刷新一次"巡检时间戳"并提交。"""
    v = os.environ.get("PICKCAT_PROBE_HEARTBEAT_HOURS")
    if v is None:
        ef = HERE / ".env"
        if ef.exists():
            for line in ef.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("PICKCAT_PROBE_HEARTBEAT_HOURS="):
                    v = line.split("=", 1)[1].strip().strip('"').strip("'")
    try:
        return float(v)
    except (TypeError, ValueError):
        return 12.0


def _last_docs_commit_ts():
    """docs/api-snapshot.json 最后一次提交的时间戳 (没提交过则 0)。"""
    r = _git("log", "-1", "--format=%ct", "--", "docs/api-snapshot.json")
    try:
        return int((r.stdout or "0").strip().splitlines()[0])
    except (ValueError, IndexError):
        return 0


def heartbeat_due(force=False):
    """是否该做一次"心跳提交" (只刷新巡检时间戳/耗时, 不记 CHANGELOG)。

    作用: 让仓库里的 API-STATUS.md 永远带一个"多久之前巡检过"的时间戳,
    而不是停留在最后一次有语义变化的那一轮 —— 否则文档看起来像很久没更新了。
    """
    if force:
        return True
    hours = _heartbeat_hours()
    if hours <= 0:
        return False
    last = _last_docs_commit_ts()
    if not last:
        return True
    return (time.time() - last) >= hours * 3600


def maybe_autopush(changes, snap, heartbeat=False):
    """探测到变化时, 把文档变更自动提交并推上 GitHub (让"接口清单"永远是最新的)。

    需要 .env 里放 GITHUB_TOKEN, 且 PICKCAT_PROBE_AUTOPUSH=1 才启用。
    任何失败都只是警告, 不影响巡检结果。
    """
    if not changes and not heartbeat:
        return None
    if not _autopush_enabled():
        return None
    token = _env_token()
    if not token:
        print("⚠ 自动推送已启用但 .env 里没有 GITHUB_TOKEN, 跳过推送。")
        return None

    def run(cmd, **kw):
        return subprocess.run(cmd, cwd=str(HERE), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", **kw)

    try:
        run(["git", "add", "docs/api-snapshot.json", "docs/API-STATUS.md",
             "docs/API-CHANGELOG.md"])
        if changes:
            msg = f"docs: API 巡检发现 {len(changes)} 处变化 ({time.strftime('%Y-%m-%d %H:%M')})"
        else:
            msg = f"docs: API 巡检刷新 (无变化, 更新巡检时间 {time.strftime('%Y-%m-%d %H:%M')})"
        r = run(["git", "commit", "-m", msg])
        if r.returncode != 0 and "nothing to commit" not in (r.stdout + r.stderr):
            print("⚠ git commit 失败:", (r.stderr or r.stdout)[:200])
            return None
        url = "https://xingguangcpp:" + token + "@github.com/xingguangcpp/pickcat-tools.git"
        # 本仓库可能有**另一个自动化**在同时 commit/push(嘴替任务)。
        # 直接 push 会撞 non-fast-forward 而失败 -> 连续失败会被平台自动暂停
        # (真踩过: 巡检从 11-03 22:16 起就没再跑过)。所以先 fetch+rebase 再推, 失败重试一次。
        r2 = None
        for attempt in range(2):
            run(["git", "fetch", "origin", "main"])
            run(["git", "rebase", "origin/main"])
            r2 = run(["git", "-c", "http.sslVerify=false", "push", url, "HEAD:main"])
            if r2.returncode == 0:
                break
            err = (r2.stderr or r2.stdout)
            if "nothing to commit" in err or "up to date" in err:
                print("✅ 远端已是最新, 无需推送")
                save_published_baseline(snap)
                return msg
            if attempt == 0:
                print("  推送冲突(可能有其他自动化在推), rebase 后重试…")
                continue
            print("⚠ 自动推送失败:", err[:200].replace(token, "***"))
            return None
        if r2 is not None and r2.returncode == 0:
            print("✅ 已自动提交并推送到 GitHub")
            # 只有真的推上去了, 才把这轮快照记为"已发布基线"。
            # 推送失败时基线不动 -> 下一轮仍拿旧基线对比 -> 这组变化会被重新
            # 检测到并重试推送, 不会被静默吞掉。
            save_published_baseline(snap)
            return msg
        print("⚠ 自动推送失败:", ((r2.stderr or r2.stdout)[:200]).replace(token, "***"))
        return None
    except Exception as e:
        print("⚠ 自动推送异常:", str(e)[:160])
        return None


def main():
    ap = argparse.ArgumentParser(description="Pickcat API 巡检器 (只读探测 + 自动记录变化)")
    ap.add_argument("--json", action="store_true", help="额外打印机器可读结果")
    ap.add_argument("--always", action="store_true", help="无变化也记一条")
    ap.add_argument("--list", action="store_true", help="只打印探测清单")
    args = ap.parse_args()

    if args.list:
        for label, path, public in PROBES:
            print(f"  {'公开' if public else '需登录':<6} {path:<52} {label}")
        return

    down, ost = service_is_down()
    if down:
        print(f"⛔ 服务不可用 (连续失败 {ost.get('consecutive_failures')} 次), 跳过本轮探测。")
        print(f"   最后失败: {ost.get('last_fail_at')}  冷却期结束后自动再试。")
        return

    c = build_client()
    ctx = build_context(c)
    probes = {}
    disc_state = load_discriminators()
    print(f"探测 {len(PROBES)} 个端点 (只读)…")
    for label, path, public in PROBES:
        real = _subst(path, ctx)
        # 分组维度沿用上次定下来的那个 (锁住, 免得窗口内容一变维度就翻转)
        rec = run_probe(c, label, real, public,
                        prefer_disc=disc_state.get(path))
        probes[f"GET {path}"] = rec
        g = rec.get("itemGroups") or {}
        if g:
            disc_state[path] = g.get("discriminator")
        flag = "OK " if rec.get("status") == 200 else str(rec.get("status"))
        print(f"  {flag:>4} {rec.get('latencyMs', 0):>5}ms  {real}")
        time.sleep(0.3)

    # --- 匿名复测: 实测每条路由的鉴权属性 (不靠手写标注) ---
    anon = PickcatClient(base=c.base, min_interval=0, verbose=False)
    auth = {}
    plan = anon_probe_plan()
    print(f"匿名复测 {len(plan)} 条路由的鉴权属性…")
    for label, path in plan:
        real = _subst(path, ctx)
        route = path.split("?", 1)[0]
        rec = run_probe(anon, label, real, True)
        mode = interpret_auth(rec.get("status"), None)
        declared = "公开" if _declared_public(route) else "需登录"
        auth[route] = {
            "label": label,
            "anonStatus": rec.get("status"),
            "anonCode": rec.get("code"),
            "mode": mode,
            "declared": declared,
            # 标注与实测是否矛盾。存进快照是为了让 diff 只在"翻转"时报一次,
            # 而不是每轮都重复报同一条 (否则工作区永远 dirty, 提交噪音不断)。
            "mismatch": bool(mode in ("公开", "需登录") and declared != mode),
        }
        flag = "OK " if rec.get("status") == 200 else str(rec.get("status"))
        print(f"  {flag:>4} {rec.get('latencyMs', 0):>5}ms  [匿名] {real}")
        time.sleep(0.3)

    snap = {
        "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "serviceUp": True,
        "schemaVersion": SCHEMA_VERSION,
        "probes": probes,
        "auth": auth,
    }
    # 分组维度锁存 (即使这轮没变化也要存, 免得下轮重新探测时维度翻转)
    save_discriminators(disc_state)
    old = load_baseline()
    changes = diff_snapshots(old, snap)

    # 心跳: 即使没有语义变化, 也定期把"巡检时间"提交上去, 让仓库里的状态表
    # 始终带着一个新鲜的时间戳 (否则它看起来像很久没巡检过)。
    beat = heartbeat_due(force=args.always)

    # 只有"确实要提交"时才重写生成物。否则每轮都重写一遍时间戳/耗时,
    # 提交又只在有变化时发生 -> 工作区永远 dirty, 徒增 diff 噪音。
    if changes or beat:
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(json.dumps(snap, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        write_status_md(snap)
    appended = append_changelog(changes, snap)

    print("-" * 56)
    if changes:
        print(f"发现 {len(changes)} 处变化:")
        for ch in changes:
            print("  - " + ch)
    else:
        print("与上次快照相比无变化。")
    if appended:
        print(f"已追加 -> {CHANGELOG_MD.name}")
    if changes or beat:
        print(f"已更新 -> {SNAPSHOT_PATH.name}, {STATUS_MD.name}")
    if not changes and beat:
        print(f"无变化, 但已到心跳间隔 ({_heartbeat_hours():g}h), 刷新巡检时间戳并提交。")
    maybe_autopush(changes, snap, heartbeat=beat)
    if args.json:
        print(json.dumps({"changes": changes, "snapshot": snap},
                         ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
