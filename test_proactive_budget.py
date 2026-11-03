#!/usr/bin/env python3
"""验证"主动式回复有单独上限, 把额度预留给'有人回我'" (纯本地, 不发网络请求)。

背景 (2026-11-03 22:xx 查出的真问题):
回复额度 12 条/天是**一个池子**, 反应式(有人回我) 和主动式(我自选话题)
抢同一个池子。而"有人回我"的需求是**双峰**的 —— 实测通知集中在
10-12 点 (15 条) 和 20-21 点 (12 条), 凌晨 00-09 点几乎没有 (7 条)。

11-03 的实际情况: 额度在 00:53-08:48 就被花光, 其中 **8 条是主动式**
(凌晨正是没人回我的时段); 11:42 之后陆续来的 5 个真·回我的人, 一个都没回上。
之后连续 8 轮全是零输出 (额度 12/12 已满)。

**额度不是不够, 是用在了错的时间、错的对象上。**
所以修法不是加额度 (用户定的 12 条不能动), 而是**分配**:
主动式单独限量, 反应式直接吃总池子。

要点:
  1. 反应式/主动式用**客观口径**判定 (目标楼层的父楼层作者是我),
     与 check 的 isReplyToMe 同一套判据 —— 记账口径和需求口径必须对得上
  2. 判不准时当反应式 (宁可少发主动内容, 也不让人干等)
  3. 主动式超限 -> 被拦; 反应式不受主动式上限影响 (它吃总池子)
  4. 总上限 12/天 一条没动
  5. 账本记 react 字段, 主动式用量能从账本重建 (账本是权威源)
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
    """模拟一个帖子的楼层: 有人回我的楼 (父楼层=ME) 和回别人的楼 (父楼层=别人)。"""

    def __init__(self):
        self.calls = []
        self.top_level = 0
        # 楼层表: postNumber -> (authorId, replyToPostNumber)
        self.floors = {
            1: ("TOPIC_OWNER", None),   # 首楼 (不在 items 里, 由 get_topic 提供)
            5: ("ME", 4),                # 我发的
            6: ("OTHER", 5),             # 别人回我的 #5  -> 回我
            7: ("OTHER2", 4),            # 别人回别人的 #4 -> 不是回我
        }

    def get_session(self):
        return {"user": {"id": "ME"}}

    def get_topic(self, tid):
        return {"title": "T", "author": {"id": "TOPIC_OWNER"}}

    def get_topic_posts(self, tid, limit=100, sort=None, cursor=None):
        # 首楼(#1)服务端不返回; 楼中楼藏在 children 里
        items = [
            {"id": "p4", "postNumber": 4, "author": {"id": "TOPIC_OWNER"},
             "replyToPostNumber": 1, "createdAt": "2026-11-03T00:00:00.000Z"},
            {"id": "p5", "postNumber": 5, "author": {"id": "ME"},
             "replyToPostNumber": 4, "createdAt": "2026-11-03T00:00:00.000Z"},
            {"id": "p6", "postNumber": 6, "author": {"id": "OTHER"},
             "replyToPostNumber": 5, "createdAt": "2026-11-03T00:00:00.000Z"},
            {"id": "p7", "postNumber": 7, "author": {"id": "OTHER2"},
             "replyToPostNumber": 4, "createdAt": "2026-11-03T00:00:00.000Z"},
        ]
        return {"items": items, "pageInfo": {"hasNextPage": False}}

    def create_reply(self, tid, body, reply_to_post_number=None, channel=None,
                     react=None, **kw):
        self.calls.append({"reply_to": reply_to_post_number, "channel": channel,
                           "react": react})
        if reply_to_post_number in (None, 1):
            self.top_level += 1
        return {"status": "PUBLISHED", "postId": "x"}


def run_reply(client, tmp, reply_to, daily=None, notify=None, ledger=None):
    """在隔离的 state 目录里跑一次 cmd_reply。"""
    d = Path(tempfile.mkdtemp(dir=tmp))
    B.state_file = lambda base: d / base
    C.state_file = lambda base: d / base
    if ledger is not None:
        C.ledger_read = lambda: list(ledger)
    st = {"daily": {"date": B.today_key(), "comments": 0, "replies": 0}, "watched": {}}
    if daily:
        st["daily"].update(daily)
    B.save_state(st)
    args = types.SimpleNamespace(
        topic_id="t1", reply_to=reply_to, body="x", title="", notify=notify
    )
    blocked = False
    try:
        B.cmd_reply(client, args)
    except SystemExit as e:
        blocked = e.code == 3
    after = json.loads((d / ".bot_state.json").read_text(encoding="utf-8"))
    return blocked, list(client.calls), after["daily"]


TMP = tempfile.mkdtemp()
TODAY = B.today_key()

print("=== 测试 1: 反应式/主动式判定 (父楼层作者是我 -> 反应式) ===")
c1 = FakeClient()
check("回我 #6 的父楼层是我 -> 反应式",
      B._reply_is_reactive(c1, "t1", 6, "ME") is True)
check("回别人 #7 的父楼层是别人 -> 主动式",
      B._reply_is_reactive(c1, "t1", 7, "ME") is False)
check("带 --notify -> 反应式 (服务端权威)",
      B._reply_is_reactive(c1, "t1", 7, "ME", notify_id="nid-1") is True)
check("楼层取不到 -> 判不准(None, 调用方按反应式放行)",
      B._reply_is_reactive(c1, "t1", 999, "ME") is None)


class BrokenClient(FakeClient):
    def get_topic_posts(self, tid, limit=100, sort=None, cursor=None):
        raise C.PickcatError("boom")


check("楼层列表请求失败 -> 判不准(None)",
      B._reply_is_reactive(BrokenClient(), "t1", 7, "ME") is None)

print("\n=== 测试 2: 主动式达到上限 -> 被拦; 反应式不受影响 ===")
# 账本里预置 3 条主动式 (上限 = 12 - 9 = 3)
pro_rows = [
    {"kind": "reply", "channel": "reply", "replyTo": 7, "react": False,
     "at": f"{TODAY} 01:00:00"},
    {"kind": "reply", "channel": "reply", "replyTo": 7, "react": False,
     "at": f"{TODAY} 02:00:00"},
    {"kind": "reply", "channel": "reply", "replyTo": 7, "react": False,
     "at": f"{TODAY} 03:00:00"},
]
orig_read = C.ledger_read
C.ledger_read = lambda: list(pro_rows)
try:
    c2 = FakeClient()
    blocked, calls, daily = run_reply(c2, TMP, 7, ledger=pro_rows)
    check("主动式第 4 条被拦", blocked, f"calls={calls}")
    check("没有发出任何请求", not calls, f"calls={calls}")
    check("回复计数没变", daily["replies"] == 0, f"got {daily['replies']}")

    c3 = FakeClient()
    blocked3, calls3, daily3 = run_reply(c3, TMP, 6, ledger=pro_rows)
    check("反应式仍然可发 (它吃总池子, 不受主动式上限影响)", not blocked3)
    check("确实发出去了", len(calls3) == 1, f"calls={calls3}")
    check("账本记 react=True", calls3 and calls3[0]["react"] is True, f"calls={calls3}")
finally:
    C.ledger_read = orig_read

print("\n=== 测试 3: 主动式没超限 -> 可发 ===")
c4 = FakeClient()
blocked4, calls4, daily4 = run_reply(c4, TMP, 7)
check("没被拦", not blocked4)
check("发出去了", len(calls4) == 1, f"calls={calls4}")
check("账本记 react=False", calls4 and calls4[0]["react"] is False, f"calls={calls4}")

print("\n=== 测试 4: 总上限 12/天 一条没动 ===")
c5 = FakeClient()
blocked5, calls5, daily5 = run_reply(c5, TMP, 6, daily={"replies": 12})
check("回复满 12 -> 被拦", blocked5)
check("没有发出任何请求", not calls5, f"calls={calls5}")

print("\n=== 测试 5: 主动式用量从账本重建 (账本是权威源) ===")
rows = [
    # 反应式: 不占主动式额度
    {"kind": "reply", "channel": "reply", "replyTo": 6, "react": True,
     "at": f"{TODAY} 01:00:00"},
    # 主动式: 占额度
    {"kind": "reply", "channel": "reply", "replyTo": 7, "react": False,
     "at": f"{TODAY} 02:00:00"},
    # 顶层评论: 走评论额度, 不算主动式
    {"kind": "reply", "channel": "comment", "replyTo": 1, "react": False,
     "at": f"{TODAY} 03:00:00"},
    # 昨天的不算
    {"kind": "reply", "channel": "reply", "replyTo": 7, "react": False,
     "at": "2020-01-01 02:00:00"},
]
C.ledger_read = lambda: list(rows)
try:
    n = B.ledger_daily_proactive_count(TODAY)
    check("只数主动式楼中楼 (1 条)", n == 1, f"got {n}")
finally:
    C.ledger_read = orig_read

print("\n=== 测试 6: 老记录 (没有 react 字段) 保守算主动式 ===")
legacy = [
    {"kind": "reply", "channel": "reply", "replyTo": 6, "at": f"{TODAY} 01:00:00"},
]
C.ledger_read = lambda: list(legacy)
try:
    n = B.ledger_daily_proactive_count(TODAY)
    check("缺失 react -> 算主动式 (保守: 少发主动, 不挤占反应式)", n == 1, f"got {n}")
finally:
    C.ledger_read = orig_read

print(f"\n{'=' * 50}")
print(f"通过 {PASSES} / 失败 {FAILS}")
sys.exit(1 if FAILS else 0)
