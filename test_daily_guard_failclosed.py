#!/usr/bin/env python3
"""验证"每日额度护栏在计数失败时必须 fail closed" (纯本地, 不发任何网络请求)。

背景: 2026-11-04 01:xx 查出 — 每日评论<=3 / 回复<=12 是用户定的**硬规矩**,
但计数有两条"查不到用量"的路径, 原实现都当成"今天一条都没发":

  ① ledger_daily_counts() 抛异常 (读端出错/参数不对)
     -> daily_guard 里 `except Exception: led_c, led_r = 0, 0`
     -> 计数从"查不到"变成"0"
  ② 账本里某条记录的 at (日期) 字段坏掉
     -> 原实现 `at[:10] != day -> continue` 把这条记录**整条丢掉**
     -> 今天的真实用量被少算

只要 state 同时也丢了 (load_state 返回 {}), 两边都是 0/0 ->
**硬上限静默突破** (当天实际能再发 3 评论 + 12 回复 = 6/24, 超一倍)。

失效方向仍然是"放行更多" —— 与前几轮修的 state/账本原子写、读端逐条容错
是同一个根因家族。

修法:
  ① 计数抛异常 -> 与"账本读不出"同档, fail closed (按额度已满处理) + 标 daily_unknown
  ② 日期读不出来的记录 -> 用**相邻记录夹逼**归属, 不再整条丢掉
     (账本按时间追加写, seq 单调递增, 坏记录的日期必落在前后可读记录之间)

要点:
  1. 计数失败 + state 丢失 -> 按已满处理 (不放行)
  2. 计数失败 + state 还有今天的计数 -> 沿用 state (不误伤)
  3. 日期坏的记录不能凭空消失 (夹逼归属)
  4. 日期都好时, 归属结果与旧实现完全一致 (不改变正常行为)
  5. 坏记录恰好被数一次 (不重复计数)
"""
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pickcat_bot as B  # noqa: E402
import pickcat_client as C  # noqa: E402

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


def rec(at, reply_to=2, channel=None, kind="reply", seq=None, react=None):
    r = {"kind": kind, "replyTo": reply_to, "at": at}
    if channel:
        r["channel"] = channel
    if seq is not None:
        r["seq"] = seq
    if react is not None:
        r["react"] = react
    return r


class FakeLedger:
    """替换 pickcat_client.ledger_read, 让测试能喂任意账本内容。"""

    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        self.orig = C.ledger_read
        C.ledger_read = lambda: list(self.rows)
        return self

    def __exit__(self, *a):
        C.ledger_read = self.orig
        return False


class ExplodingLedger:
    """让 ledger_read 直接抛异常 (模拟读端故障)。"""

    def __enter__(self):
        self.orig = C.ledger_read

        def boom():
            raise OSError("disk gone")

        C.ledger_read = boom
        return self

    def __exit__(self, *a):
        C.ledger_read = self.orig
        return False


TODAY = B.today_key()
YESTERDAY = "2026-01-01"

print("=== 测试 1: 计数失败 + state 丢失 -> 必须 fail closed (不静默放行) ===")
orig_counts = B.ledger_daily_counts


def boom_counts(day):
    raise ValueError("count failed")


B.ledger_daily_counts = boom_counts
try:
    with FakeLedger([]):
        st = {}  # state 丢失/损坏
        d = B.daily_guard(st, "replies")
    check(
        "计数失败时回复数按已满处理 (12)",
        d["replies"] == B.MAX_REPLIES_PER_DAY,
        f"got {d['replies']}",
    )
    check(
        "计数失败时评论数按已满处理 (3)",
        d["comments"] == B.MAX_COMMENTS_PER_DAY,
        f"got {d['comments']}",
    )
    check("标出 daily_unknown (让人知道是查不到)", d.get("daily_unknown") is True)
finally:
    B.ledger_daily_counts = orig_counts

print("\n=== 测试 2: 计数失败 + state 还有今天的计数 -> 沿用 state (不误伤) ===")
B.ledger_daily_counts = boom_counts
try:
    with FakeLedger([]):
        st = {"daily": {"date": TODAY, "comments": 2, "replies": 5}}
        d = B.daily_guard(st, "replies")
    check("沿用 state 的回复计数 (5)", d["replies"] == 5, f"got {d['replies']}")
    check("沿用 state 的评论计数 (2)", d["comments"] == 2, f"got {d['comments']}")
    check("不误标 daily_unknown", not d.get("daily_unknown"))
finally:
    B.ledger_daily_counts = orig_counts

print("\n=== 测试 3: 账本读不出来 (read 抛异常) 也必须 fail closed ===")
with ExplodingLedger():
    st = {}
    d = B.daily_guard(st, "replies")
check(
    "账本读不出时按已满处理",
    d["replies"] == B.MAX_REPLIES_PER_DAY,
    f"got {d['replies']}",
)
check("标出 daily_unknown", d.get("daily_unknown") is True)

print("\n=== 测试 4: 日期读不出来的记录不能凭空消失 (夹逼归属) ===")
# 坏日期的记录夹在两条今天的记录中间 -> 必须被计入今天
rows = [
    rec(f"{TODAY} 01:00:00", reply_to=1, channel="comment", seq=1),
    rec("", reply_to=2, channel="reply", seq=2),  # at 是空串 -> 日期读不出
    rec(f"{TODAY} 03:00:00", reply_to=3, channel="reply", seq=3),
]
with FakeLedger(rows):
    c, r = B.ledger_daily_counts(TODAY)
check("坏日期的记录被计入今天 (reply 2 条)", r == 2, f"got {r}")
check("坏日期的记录没影响 comment 计数", c == 1, f"got {c}")

print("\n=== 测试 5: 坏日期记录夹在昨天与今天之间 -> 计入区间内的天 ===")
rows = [
    rec(f"{YESTERDAY} 01:00:00", reply_to=1, channel="comment", seq=1),
    rec("not-a-date", reply_to=2, channel="reply", seq=2),
    rec(f"{TODAY} 03:00:00", reply_to=3, channel="reply", seq=3),
]
with FakeLedger(rows):
    c, r = B.ledger_daily_counts(TODAY)
check("区间含今天 -> 坏记录计入今天", r == 2, f"got {r}")
with FakeLedger(rows):
    c2, r2 = B.ledger_daily_counts(YESTERDAY)
check("区间含昨天 -> 坏记录也计入昨天 (恰好一次)", r2 == 1, f"got {r2}")

print("\n=== 测试 6: 日期都好时, 行为与旧实现完全一致 (不改变正常路径) ===")
rows = [
    rec(f"{TODAY} 01:00:00", reply_to=1, channel="comment"),
    rec(f"{TODAY} 02:00:00", reply_to=15, channel="reply"),
    rec(f"{TODAY} 03:00:00", reply_to=1),  # 老记录: replyTo=1 -> comment
    rec(f"{TODAY} 04:00:00", reply_to=9),  # 老记录: replyTo>1 -> reply
    rec(f"{TODAY} 05:00:00", reply_to=1, kind="topic"),  # 发帖不算评论
    rec(f"{YESTERDAY} 06:00:00", reply_to=2, channel="reply"),  # 昨天的不算
]
with FakeLedger(rows):
    c, r = B.ledger_daily_counts(TODAY)
check("comment 计 2 条 (显式1 + 老记录推断1)", c == 2, f"got {c}")
check("reply 计 2 条 (显式1 + 老记录推断1)", r == 2, f"got {r}")

print("\n=== 测试 7: 整本账本日期都坏 -> 按最坏算 (归给今天) ===")
rows = [
    rec("", reply_to=1, channel="comment"),
    rec("garbage", reply_to=2, channel="reply"),
]
with FakeLedger(rows):
    c, r = B.ledger_daily_counts(TODAY)
check("整本坏账本: comment 计 1 条", c == 1, f"got {c}")
check("整本坏账本: reply 计 1 条", r == 1, f"got {r}")

print("\n=== 测试 8: 坏记录恰好被数一次 (不重复计数) ===")
rows = [
    rec(f"{TODAY} 01:00:00", reply_to=1, channel="comment", seq=1),
    rec("bad-date", reply_to=2, channel="reply", seq=2),
    rec(f"{TODAY} 03:00:00", reply_to=3, channel="reply", seq=3),
]
with FakeLedger(rows):
    c, r = B.ledger_daily_counts(TODAY)
total = c + r
check("今天的总数是 3 (1 comment + 2 reply), 无重复", total == 3, f"got {total}")

print("\n=== 测试 9: 坏 replyTo 仍保守按顶层评论计 (老约定不变) ===")
rows = [
    rec(f"{TODAY} 01:00:00", reply_to="abc"),
]
with FakeLedger(rows):
    c, r = B.ledger_daily_counts(TODAY)
check("replyTo 坏了 -> 计为顶层评论", c == 1 and r == 0, f"got c={c} r={r}")

print("\n=== 测试 10: 主动式计数与总计数口径一致 (同一条坏记录) ===")
rows = [
    rec(f"{TODAY} 01:00:00", reply_to=2, channel="reply", react=True, seq=1),
    rec("bad-date", reply_to=3, channel="reply", react=False, seq=2),
    rec(f"{TODAY} 03:00:00", reply_to=4, channel="reply", react=True, seq=3),
]
with FakeLedger(rows):
    reactive, proactive, unknown = B.ledger_daily_kind_counts(TODAY)
check("反应式 2 条 (坏日期那条算主动式)", reactive == 2, f"got {reactive}")
check("主动式 1 条 (坏日期那条被计入)", proactive == 1, f"got {proactive}")

print()
print(f"=== 结果: {PASSES} 通过, {FAILS} 失败 ===")
sys.exit(1 if FAILS else 0)
