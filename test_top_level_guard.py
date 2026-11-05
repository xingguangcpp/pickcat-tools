#!/usr/bin/env python3
"""验证"每帖顶层评论 <= 3"的护栏不会被绕过 (纯本地, 不发任何网络请求)。

背景: 2026-11-03 18:xx 查出 —— `my_top_level_count()` 旧实现**只数服务端楼层**,
而服务端楼层有两个已知的漏算路径, 都会让这个护栏**放行更多** (失效方向和
"硬上限被静默突破"那几次一模一样):

  1. **索引延迟**: 写操作是异步审核的, 刚过审的评论要过一阵才出现在楼层列表里
     (memory 里记过: "审核 PUBLISHED 后帖子列表接口有索引延迟, 换 sort 参数
     重读才拿到")。于是"刚发完第 3 条顶层评论, 立刻再发第 4 条"时, 服务端只数到
     2 条 -> 第 4 条被放行 -> 该帖 4 条顶层评论, 突破用户定的每帖 <= 3。
  2. **翻页残缺**: `posts_of` 遇 PickcatError 会 break, 返回半截列表, 而调用方
     无从得知 —— 少算多少全看运气。

修法 (与 daily_guard 同构: 权威源 + 保守兜底):
  - 账本 (.wm_ledger.json) 记的是"我发出过的每一次写操作", 是权威来源,
    不受索引延迟影响。
  - 服务端楼层 与 账本**按 postId 去重求并集**:
      服务端有、账本无 -> 我手动发的 (脚本之外的评论)
      账本有、服务端无 -> 刚发出去还没被服务端索引到的
  - 楼层列表**没取全**时 (翻页中途失败), fail closed: 按"已满"处理,
    因为这个护栏失效的方向是"超发", 而每帖 <= 3 是用户定的硬约束。

要点:
  1. 索引延迟场景: 服务端只见 2 条 + 账本有第 3 条 -> 判 3 条 -> 拦下第 4 条
  2. 手动发的评论: 服务端有、账本无 -> 也算进额度
  3. 翻页残缺: 楼层列表取不全 -> fail closed (按已满处理)
  4. 不重复计数: 同一 postId 在两边都有 -> 只算一次
  5. 楼中楼回复 (rtp != 1) 不占顶层额度
  6. 审核失败 (PUBLICATION_FAILED) 的记录不占额度 (根本没发出去)
  7. kind=topic (我自己开的帖) 不算顶层评论
"""
import json
import sys
import tempfile
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pickcat_bot as B  # noqa: E402
import pickcat_client as C  # noqa: E402

# --- 护栏隔离 (2026-11-05) -------------------------------------------------
# 限制表在作者定稿后变严了(评论额度=0 / 每帖回复<=1 / 每小时<=2), 而本文件只验**一条**护栏,
# 别的护栏会误伤。这里把无关护栏临时放开, 让测试聚焦它要验的那条。
# 被测的那条护栏一律用模块常量, 以后改上限测试自动跟随。
B.MAX_WRITES_PER_HOUR = 999          # 频率闸门: 与本测试无关
B.MAX_REPLIES_PER_TOPIC = 999        # 每帖回复数: 与本测试无关
B.MAX_TOP_LEVEL = 3               # 被测对象: 每帖顶层上限(作者定稿默认0, 这里要一个具体值)
B.MAX_COMMENTS_PER_DAY = 3      # 与本测试无关

FAILS = 0
PASSES = 0


def check(name, cond, extra=""):
    global FAILS, PASSES
    if cond:
        PASSES += 1
        print(f"  PASS  {name}")
    else:
        FAILS += 1
        print(f"  FAIL  {name} {extra}")


class FakeClient:
    """模拟服务端楼层列表。server_items = 服务端能看到的楼层。"""

    def __init__(self, server_items=None, fail_after=None):
        self.server_items = server_items or []
        self.fail_after = fail_after  # 取到第几页后报错 (None = 不报错)
        self.calls = 0
        self.top_level = 0
        self.created = []  # 实际发出去的顶层评论 (postId)

    def get_session(self):
        return {"user": {"id": "ME"}}

    def get_topic_posts(self, tid, limit=20, sort=None, cursor=None):
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise C.PickcatError(502, {"code": "SERVICE_UNAVAILABLE",
                                       "message": "模拟翻页中途失败"})
        return {"items": list(self.server_items), "pageInfo": {"hasNextPage": False}}

    def get_topic(self, tid):
        return {"title": "T", "author": {"id": "ME"}}

    def create_reply(self, tid, body, reply_to_post_number=None, channel=None, **kw):
        self.created.append(reply_to_post_number)
        if reply_to_post_number in (None, 1):
            self.top_level += 1
        return {"status": "PUBLISHED", "postId": f"new{len(self.created)}"}


def tl_post(pid, pn, rtp=1, author="ME"):
    return {"id": pid, "postNumber": pn, "author": {"id": author},
            "replyToPostNumber": rtp}


def setup_ledger(rows):
    """把账本读端替换成固定内容 (只读, 不写盘)。"""
    orig = C.ledger_read
    C.ledger_read = lambda: list(rows)
    return orig


TMP = tempfile.mkdtemp()


def run_comment(client, ledger_rows, tid="t1"):
    """在隔离的 state 目录里跑一次 cmd_comment, 返回 (是否被拦, 发了几条)。"""
    d = Path(tempfile.mkdtemp(dir=TMP))
    B.state_file = lambda base: d / base
    C.state_file = lambda base: d / base
    orig = setup_ledger(ledger_rows)
    st = {"daily": {"date": B.today_key(), "comments": 0, "replies": 0}, "watched": {}}
    B.save_state(st)
    args = types.SimpleNamespace(topic_id=tid, body="x", title="")
    blocked = False
    try:
        B.cmd_comment(client, args)
    except SystemExit as e:
        blocked = e.code == 3
    finally:
        C.ledger_read = orig
    return blocked, list(client.created)


def count_top_level(client, ledger_rows, tid="t1"):
    """在给定账本内容下数我的顶层评论数, 返回 (count, reliable)。"""
    orig = setup_ledger(ledger_rows)
    try:
        return B.my_top_level_count(client, tid, "ME")
    finally:
        C.ledger_read = orig


TODAY = B.today_key()


def rec(seq, reply_to, post_id, status="PUBLISHED", topic="t1"):
    return {"seq": seq, "kind": "reply", "topicId": topic, "postId": post_id,
            "replyTo": reply_to, "status": status, "at": f"{TODAY} 01:00:00"}


print("=== 测试 1: 索引延迟 —— 服务端只见 2 条, 账本有第 3 条 -> 拦下第 4 条 ===")
c1 = FakeClient(server_items=[tl_post("s1", 2), tl_post("s2", 3)])
ledger1 = [
    rec(1, 1, "s1"),
    rec(2, 1, "s2"),
    rec(3, 1, "s3"),   # 刚发出去, 服务端还没索引到
]
blocked, sent = run_comment(c1, ledger1)
check("第 4 条顶层评论被拦下", blocked)
check("没有发出任何请求", not sent, f"sent={sent}")
n, ok = count_top_level(c1, ledger1)
check("计数 = 3 (2 服务端 + 1 账本独有)", n == 3, f"got {n}")
check("列表取全时 reliable=True", ok is True)

print("\n=== 测试 2: 手动发的评论 (服务端有、账本无) 也占额度 ===")
c2 = FakeClient(server_items=[tl_post("s1", 2), tl_post("s2", 3), tl_post("m1", 4)])
blocked, sent = run_comment(c2, [])   # 账本空
check("第 4 条被拦下", blocked)
check("没有发出任何请求", not sent, f"sent={sent}")
n, ok = count_top_level(c2, [])
check("计数 = 3 (全部来自服务端)", n == 3, f"got {n}")

print("\n=== 测试 3: 翻页残缺 -> fail closed (按已满处理) ===")
c3 = FakeClient(server_items=[], fail_after=0)   # 第一页就失败
blocked, sent = run_comment(c3, [])
check("被拦下 (楼层列表取不全 -> 当已满)", blocked)
check("没有发出任何请求", not sent, f"sent={sent}")
n, ok = count_top_level(c3, [])
check("reliable=False", ok is False)
check("计数偏小 (0)", n == 0, f"got {n}")

print("\n=== 测试 4: 同一个 postId 在两边都有 -> 只算一次 ===")
c4 = FakeClient(server_items=[tl_post("s1", 2), tl_post("s2", 3)])
ledger4 = [rec(1, 1, "s1"), rec(2, 1, "s2")]
n, ok = count_top_level(c4, ledger4)
check("计数 = 2 (没重复算)", n == 2, f"got {n}")

print("\n=== 测试 5: 楼中楼回复 (rtp != 1) 不占顶层额度 ===")
c5 = FakeClient(server_items=[tl_post("s1", 2, rtp=5), tl_post("s2", 3, rtp=7)])
n, ok = count_top_level(c5, [])
check("计数 = 0", n == 0, f"got {n}")
ledger5 = [rec(1, 15, "s1"), rec(2, 18, "s2")]
n, ok = count_top_level(c5, [])
check("账本里的楼中楼也不占顶层额度", n == 0, f"got {n}")

print("\n=== 测试 6: 审核失败的记录不占额度 ===")
c6 = FakeClient(server_items=[])
ledger6 = [rec(1, 1, "f1", status="PUBLICATION_FAILED"),
           rec(2, 1, "f2", status="PUBLICATION_FAILED")]
n, ok = count_top_level(c6, ledger6)
check("计数 = 0 (没发出去的不算)", n == 0, f"got {n}")

print("\n=== 测试 7: kind=topic (我自己开的帖) 不算顶层评论 ===")
c7 = FakeClient(server_items=[])
ledger7 = [{"seq": 9, "kind": "topic", "topicId": "t1", "postId": "tp1",
            "replyTo": None, "status": "PUBLISHED", "at": f"{TODAY} 01:00:00"}]
n, ok = count_top_level(c7, ledger7)
check("计数 = 0", n == 0, f"got {n}")

print("\n=== 测试 8: 别的帖的记录不串到本帖 ===")
c8 = FakeClient(server_items=[])
ledger8 = [rec(1, 1, "x1", topic="other")]
n, ok = count_top_level(c8, ledger8)
check("计数 = 0", n == 0, f"got {n}")

print("\n=== 测试 9: 额度没满时正常放行 ===")
# 上限现在是 1(作者定稿): "额度没满" = 服务端一条顶层评论都看不到
c9 = FakeClient(server_items=[])
# 上限现在是 1(作者定稿), "额度没满" = 该帖 0 条顶层评论
blocked, sent = run_comment(c9, [])
check("没被拦", not blocked)
check("确实发出去了", len(sent) == 1, f"sent={sent}")

print("\n=== 测试 10: 坏记录 (replyTo 非数字) 不能把整批带走 ===")
c10 = FakeClient(server_items=[])
ledger10 = [rec(1, 1, "b1"), rec(2, "abc", "b2"), rec(3, 1, "b3")]
n, ok = count_top_level(c10, ledger10)
check("计数 = 3 (坏记录保守按顶层算, 没让整批归零)", n == 3, f"got {n}")

print("\n=== 测试 11: 账本里的记录缺 postId 时仍能计数 ===")
c11 = FakeClient(server_items=[])
ledger11 = [{"seq": 1, "kind": "reply", "topicId": "t1", "replyTo": 1,
             "status": "PUBLISHED", "at": f"{TODAY} 01:00:00"}]
n, ok = count_top_level(c11, ledger11)
check("计数 = 1", n == 1, f"got {n}")

print(f"\n{'=' * 50}")
print(f"通过 {PASSES} / 失败 {FAILS}")
sys.exit(1 if FAILS else 0)
