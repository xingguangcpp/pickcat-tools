#!/usr/bin/env python3
"""验证"每日额度以账本为准"逻辑 (纯本地, 不发任何网络请求)。

背景: 2026-11-03 查出 — 每日评论/回复上限是用户定的**硬规矩**, 但计数只存在
.bot_state.json 里, 而 load_state() 会吞掉 JSON 解析异常直接返回 {}。于是:

  状态文件损坏/丢失/被 gitignore 排除后没带过来
    -> load_state() 返回 {}
    -> daily_guard() 造出 {date: 今天, comments: 0, replies: 0}
    -> 当天已经用掉的 3 条评论 + 12 条回复**被静默清零**
    -> 机器人当天可以再发 3 条顶层评论 + 12 条回复 (6/24, 超一倍)

账本 (.wm_ledger.json) 记着每次写操作的通道和时间, 是权威依据。

要点:
  1. 状态文件丢失 -> 计数从账本重建, 不归零 (核心修复)
  2. 状态比账本大 -> 取状态 (账本漏记时不能把用量记小)
  3. 账本比状态大 -> 取账本 (状态漏更新时不能把用量记小)
  4. 跨天只统计当天的记录
  5. channel 字段优先; 老记录没有则按 replyTo 推断
  6. save_state 是原子的 (写临时文件 + os.replace), 不会留半截 JSON
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


def rec(at, reply_to=2, channel=None, kind="reply"):
    r = {"kind": kind, "replyTo": reply_to, "at": at}
    if channel:
        r["channel"] = channel
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
YESTERDAY = "2026-01-01"

print("=== 测试 1: channel 优先, 老记录按 replyTo 推断 ===")
rows = [
    rec(f"{TODAY} 01:00:00", reply_to=1, channel="comment"),   # 显式顶层评论
    rec(f"{TODAY} 02:00:00", reply_to=15, channel="reply"),    # 显式楼中楼
    rec(f"{TODAY} 03:00:00", reply_to=1),                      # 老记录: replyTo=1 -> comment
    rec(f"{TODAY} 04:00:00", reply_to=9),                      # 老记录: replyTo>1 -> reply
    rec(f"{TODAY} 05:00:00", reply_to=1, kind="topic"),        # 发帖不算评论
]
with FakeLedger(rows):
    c, r = B.ledger_daily_counts(TODAY)
check("comment 计 2 条 (显式1 + 老记录推断1)", c == 2, f"got {c}")
check("reply 计 2 条 (显式1 + 老记录推断1)", r == 2, f"got {r}")

print("\n=== 测试 2: 跨天只统计当天 ===")
rows = [
    rec(f"{YESTERDAY} 01:00:00", reply_to=1, channel="comment"),
    rec(f"{TODAY} 01:00:00", reply_to=1, channel="comment"),
    rec(f"{TODAY} 02:00:00", reply_to=3, channel="reply"),
]
with FakeLedger(rows):
    c, r = B.ledger_daily_counts(TODAY)
check("昨天的记录不计入今天 (comment)", c == 1, f"got {c}")
check("昨天的记录不计入今天 (reply)", r == 1, f"got {r}")

print("\n=== 测试 3: 状态文件丢失 -> 计数从账本重建, 不归零 ===")
# 这就是本轮修的核心 bug: state 没了, 但账本证明今天已经用了 3 评论 + 12 回复
rows = [rec(f"{TODAY} 0{i//10}:{i%10}:00", reply_to=1, channel="comment") for i in range(3)]
rows += [rec(f"{TODAY} 1{i//10}:{i%10}:00", reply_to=2 + i, channel="reply") for i in range(12)]
with FakeLedger(rows):
    st = {}  # 状态文件丢失/损坏 -> load_state() 返回 {}
    d = B.daily_guard(st, "replies")
check("评论计数被账本恢复为 3", d["comments"] == 3, f"got {d['comments']}")
check("回复计数被账本恢复为 12", d["replies"] == 12, f"got {d['replies']}")
check("日期仍是今天", d["date"] == TODAY, f"got {d['date']}")

print("\n=== 测试 4: 取较大值 (两边各偏一边都不该把用量记小) ===")
rows = [
    rec(f"{TODAY} 01:00:00", reply_to=1, channel="comment"),
    rec(f"{TODAY} 02:00:00", reply_to=5, channel="reply"),
]
with FakeLedger(rows):
    # 状态比账本大 -> 保留状态 (账本漏记时不能把用量记小)
    st = {"daily": {"date": TODAY, "comments": 2, "replies": 7}}
    d = B.daily_guard(st, "replies")
check("状态更大时取状态 (comments=2)", d["comments"] == 2, f"got {d['comments']}")
check("状态更大时取状态 (replies=7)", d["replies"] == 7, f"got {d['replies']}")

with FakeLedger(rows):
    # 账本比状态大 -> 取账本 (状态漏更新时不能把用量记小)
    st = {"daily": {"date": TODAY, "comments": 0, "replies": 0}}
    d = B.daily_guard(st, "replies")
check("账本更大时取账本 (comments=1)", d["comments"] == 1, f"got {d['comments']}")
check("账本更大时取账本 (replies=1)", d["replies"] == 1, f"got {d['replies']}")

print("\n=== 测试 5: 跨天清零仍正常 (昨天的账本不能算到今天头上) ===")
with FakeLedger([rec(f"{YESTERDAY} 01:00:00", reply_to=1, channel="comment")]):
    st = {"daily": {"date": YESTERDAY, "comments": 3, "replies": 12}}
    d = B.daily_guard(st, "replies")
check("跨天后评论清零", d["comments"] == 0, f"got {d['comments']}")
check("跨天后回复清零", d["replies"] == 0, f"got {d['replies']}")

print("\n=== 测试 6: save_state 原子写 (不留半截 JSON, 不留临时文件) ===")
tmpdir = tempfile.mkdtemp()
orig_state_file = B.state_file


def fake_state_file(base):
    return Path(tmpdir) / base


B.state_file = fake_state_file
try:
    B.save_state({"daily": {"date": TODAY, "comments": 1, "replies": 2}, "x": "y"})
    p = Path(tmpdir) / ".bot_state.json"
    check("状态文件已生成", p.exists())
    loaded = json.loads(p.read_text(encoding="utf-8"))
    check("内容可解析且正确", loaded["daily"]["comments"] == 1 and loaded["x"] == "y")
    leftovers = [f for f in os.listdir(tmpdir) if f.endswith(".tmp")]
    check("没有残留 .tmp 文件", not leftovers, f"got {leftovers}")

    # 旧内容必须被完整覆盖 (不能是追加/半截)
    p.write_text("{\"daily\": {\"date\": \"2000-01-01\", \"comments\": 99, "
                 "\"replies\": 99}, \"junk\": \"" + "x" * 500 + "\"}", encoding="utf-8")
    B.save_state({"daily": {"date": TODAY, "comments": 3, "replies": 12}})
    loaded2 = json.loads(p.read_text(encoding="utf-8"))
    check("覆盖后是完整的新内容 (不是旧文件的残留)",
          loaded2["daily"]["comments"] == 3 and "junk" not in loaded2)
finally:
    B.state_file = orig_state_file

print("\n=== 测试 7: 真实账本与真实状态一致 (不改动任何文件) ===")
try:
    from pickcat_client import ledger_read as real_ledger_read

    real_rows = real_ledger_read()
    lc, lr = B.ledger_daily_counts(TODAY)
    stf = orig_state_file(".bot_state.json")
    if stf.exists():
        real_state = json.loads(stf.read_text(encoding="utf-8"))
        rd = real_state.get("daily", {})
        same = (rd.get("date") == TODAY
                and int(rd.get("comments", 0)) == lc
                and int(rd.get("replies", 0)) == lr)
        check("账本重算的今日计数 == 状态文件里的计数", same,
              f"ledger=({lc},{lr}) state=({rd.get('comments')},{rd.get('replies')})")
    else:
        check("真实账本可读", isinstance(real_rows, list))
except Exception as e:
    check("真实账本校验未抛异常", False, str(e))

print(f"\n{'=' * 50}")
print(f"通过 {PASSES} / 失败 {FAILS}")
sys.exit(1 if FAILS else 0)
