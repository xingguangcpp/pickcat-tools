#!/usr/bin/env python3
"""验证"账本脏数据不会让每日硬上限静默失效" (纯本地, 不发任何网络请求)。

背景 (2026-11-03 15:xx 查出): 每日评论/回复上限的权威来源是账本
.wm_ledger.json, 但读它的地方有两处**脆**的写法:

  1. ledger_daily_counts(): `int(rt)` —— 账本里任何一条记录的 replyTo
     写成非数字 (如 "abc"), 整个函数抛 ValueError。
     daily_guard() 把这个异常吞掉 (except: pass) -> 当天计数退回 state 的值。
     state 也丢了 (load_state 返回 {}) 时就是 0/0 ->
     **硬上限 (评论<=3 / 回复<=12) 被静默突破**, 失效方向仍是"放行更多"。

  2. answered_floor_numbers(): 账本那段的 try 包着**整个 for 循环**,
     一条坏记录抛异常 -> 后面所有记录的 replyTo 全丢 ->
     "我回过的楼层"被判成没回过 -> 下一轮重复作答, 白花回复额度。

  3. ledger_read() 在主文件与 .bak 都解析失败时返回 [] —— 这是"故障",
     不是"今天没用过"。若 state 同时丢了, 同样 0/0。

要点:
  1. 坏记录 (replyTo 非数字) 不让整天计数归零 —— 其余记录照常统计
  2. 坏记录的通道判定保守 (按顶层评论计, 上限更严)
  3. 坏记录不让 answered_floor_numbers 丢掉后续记录的 replyTo
  4. 账本读不出来 + state 也没记录 -> fail closed (按已满处理)
  5. 账本读不出来 + state 还有今天的计数 -> 用 state (不误伤)
  6. 真实账本/状态不受影响 (不改动任何文件)
"""
import json
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


def rec(at, reply_to=2, channel=None, kind="reply", **kw):
    r = {"kind": kind, "replyTo": reply_to, "at": at}
    if channel:
        r["channel"] = channel
    r.update(kw)
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


TODAY = B.today_key()

print("=== 测试 1: 一条坏记录不能让整天计数归零 ===")
# 这就是本轮修的核心 bug: replyTo="abc" 让旧实现整个函数抛 ValueError,
# daily_guard 吞掉 -> state 丢了就是 0/0 -> 硬上限被静默突破。
rows = [
    rec(f"{TODAY} 01:00:00", reply_to=1, channel="comment"),
    rec(f"{TODAY} 02:00:00", reply_to="abc"),          # 坏记录
    rec(f"{TODAY} 03:00:00", reply_to=5, channel="reply"),
    rec(f"{TODAY} 04:00:00", reply_to=7, channel="reply"),
]
with FakeLedger(rows):
    try:
        c, r = B.ledger_daily_counts(TODAY)
        raised = False
    except Exception as e:
        c, r = 0, 0
        raised = True
        print(f"    (raised {type(e).__name__}: {e})")
check("坏记录没有让整个函数抛异常", not raised)
check("其余记录照常统计 (comment=2: 1条正常 + 1条坏记录保守计)", c == 2, f"got {c}")
check("其余记录照常统计 (reply=2)", r == 2, f"got {r}")

print("\n=== 测试 2: 坏记录的通道判定保守 (按顶层评论计) ===")
# 顶层评论上限 3 比回复上限 12 严, 认不准时宁可少放行也不多放行。
rows = [
    rec(f"{TODAY} 01:00:00", reply_to="abc"),   # 坏记录 -> 保守算 comment
    rec(f"{TODAY} 02:00:00", reply_to=9, channel="reply"),
]
with FakeLedger(rows):
    c, r = B.ledger_daily_counts(TODAY)
check("坏记录被计为顶层评论 (comment=1)", c == 1, f"got {c}")
check("坏记录没被误算成楼中楼 (reply=1)", r == 1, f"got {r}")

print("\n=== 测试 3: 坏记录不拖累其他坏状态 (at 格式异常) ===")
rows = [
    rec(f"{TODAY} 01:00:00", reply_to=1, channel="comment"),
    {"kind": "reply", "replyTo": 3, "at": None},       # at 为 None
    {"kind": "reply", "replyTo": 4},                   # 没有 at
    rec(f"{TODAY} 05:00:00", reply_to=5, channel="reply"),
]
with FakeLedger(rows):
    c, r = B.ledger_daily_counts(TODAY)
check("at 异常的记录被跳过而不抛异常 (comment=1)", c == 1, f"got {c}")
check("其余记录不受影响 (reply=1)", r == 1, f"got {r}")

print("\n=== 测试 4: 坏记录不让 answered_floor_numbers 丢掉后续记录 ===")
# 旧实现: 账本那段的 try 包着整个 for 循环, 一条坏记录抛异常 ->
# 后面所有记录的 replyTo 全丢 -> "我回过的楼层"判错 -> 重复作答白花额度。
bad = rec(f"{TODAY} 01:00:00", reply_to="abc", topicId="T1")
good = rec(f"{TODAY} 02:00:00", reply_to=42, topicId="T1")
later = rec(f"{TODAY} 03:00:00", reply_to=77, topicId="T1")


class ExplodingClient:
    """服务端楼层拉不到 (抛 PickcatError) —— 只验证账本那一路判据。"""

    def get_topic_posts(self, tid, limit=20, sort=None, cursor=None):
        raise C.PickcatError(500, {"code": "SERVER_DOWN", "message": "boom"})


orig_lread = C.ledger_read
C.ledger_read = lambda: [bad, good, later]
try:
    got = B.answered_floor_numbers(ExplodingClient(), "T1", "ME")
except Exception as e:
    got = set()
    print(f"    (raised {type(e).__name__}: {e})")
finally:
    C.ledger_read = orig_lread
check("坏记录之后的记录仍被读到 (42)", 42 in got, f"got {sorted(got)}")
check("坏记录之后的第二条也被读到 (77)", 77 in got, f"got {sorted(got)}")
check("坏记录本身不产生脏数据", "abc" not in got, f"got {sorted(got)}")

print("\n=== 测试 5: 账本读不出来 + state 也丢 -> fail closed ===")
# ledger_read() 在主文件和 .bak 都解析失败时返回 [] —— 那是"故障",
# 不是"今天没用过"。若当成 0, state 也丢时就是 0/0 -> 硬上限突破。
tmpdir = tempfile.mkdtemp()
orig_state_file = B.state_file
orig_client_state_file = C.state_file


def fake_state_file(base):
    return Path(tmpdir) / base


B.state_file = fake_state_file
C.state_file = fake_state_file
try:
    # 账本文件存在、非空, 但内容是坏的 -> ledger_read() 返回 []
    lp = Path(tmpdir) / ".wm_ledger.json"
    lp.write_text("{{{ not json at all", encoding="utf-8")
    check("坏账本让 ledger_read() 返回空列表", C.ledger_read() == [])
    check("坏账本被识别为不可读", C.ledger_unreadable() is True)

    # state 也丢 (模拟 load_state 返回 {})
    st = {}
    d = B.daily_guard(st, "replies")
    check("fail closed: 评论按已满处理", d["comments"] >= B.MAX_COMMENTS_PER_DAY,
          f"got {d['comments']}")
    check("fail closed: 回复按已满处理", d["replies"] >= B.MAX_REPLIES_PER_DAY,
          f"got {d['replies']}")
    check("标出了 daily_unknown (让人知道是查不到)", d.get("daily_unknown") is True,
          f"got {d.get('daily_unknown')}")

    print("\n=== 测试 6: 账本读不出来但 state 还有今天的计数 -> 用 state, 不误伤 ===")
    st = {"daily": {"date": TODAY, "comments": 2, "replies": 5}}
    d = B.daily_guard(st, "replies")
    check("沿用 state 的计数 (comments=2)", d["comments"] == 2, f"got {d['comments']}")
    check("沿用 state 的计数 (replies=5)", d["replies"] == 5, f"got {d['replies']}")
    check("不标 daily_unknown (state 够用)", not d.get("daily_unknown"),
          f"got {d.get('daily_unknown')}")

    print("\n=== 测试 7: 账本文件根本不存在 -> 不算故障 (全新环境) ===")
    lp.unlink()
    check("没有账本文件时不算不可读", C.ledger_unreadable() is False)
    st = {}
    d = B.daily_guard(st, "replies")
    check("全新环境计数为 0 (可正常写)", d["comments"] == 0 and d["replies"] == 0,
          f"got ({d['comments']},{d['replies']})")
    check("全新环境不标 daily_unknown", not d.get("daily_unknown"))
finally:
    B.state_file = orig_state_file
    C.state_file = orig_client_state_file

print("\n=== 测试 8: 真实账本与真实状态仍然一致 (不改动任何文件) ===")
try:
    lc, lr = B.ledger_daily_counts(TODAY)
    stf = C.state_file(".bot_state.json")
    if stf.exists():
        real_state = json.loads(stf.read_text(encoding="utf-8"))
        rd = real_state.get("daily", {})
        same = (rd.get("date") == TODAY
                and int(rd.get("comments", 0)) == lc
                and int(rd.get("replies", 0)) == lr)
        check("账本重算的今日计数 == 状态文件里的计数", same,
              f"ledger=({lc},{lr}) state=({rd.get('comments')},{rd.get('replies')})")
    check("真实账本可读 (非空)", len(C.ledger_read()) > 0)
    check("真实账本未被判为不可读", C.ledger_unreadable() is False)
except Exception as e:
    check("真实账本校验未抛异常", False, str(e))

print(f"\n{'=' * 50}")
print(f"通过 {PASSES} / 失败 {FAILS}")
sys.exit(1 if FAILS else 0)
