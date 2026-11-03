#!/usr/bin/env python3
"""验证"顶层评论走评论预算、楼中楼走回复预算" (纯本地, 不发任何网络请求)。

背景: 2026-11-03 12:44 查出 —— cmd_reply 里"顶层评论"和"楼中楼回复"两条路径
虽然都检查了每日上限, 但顶层评论那条路径查的是**回复**额度 (MAX_REPLIES_PER_DAY=12),
不是**评论**额度 (MAX_COMMENTS_PER_DAY=3)。于是:

    reply --reply-to 1   (顶层评论)
      -> 只受"每日回复 12 条"限制
      -> 一天能发 12 条顶层评论, 而不是用户定的 3 条

用户定的硬约束是"每日评论上限 3、每日回复上限 12"。顶层评论 (replyToPostNumber
指向首楼或为空) 在服务端就是**顶层评论**, 和 comment 命令发出去的东西一模一样,
所以它必须占评论额度。绕过路径不是理论上的: check 报的待回复里就有 rtp=1 的
"顶层评论"型回信, 早期轮次为了回它就会用 --reply-to 1。

要点:
  1. reply --reply-to 1/0/None -> 顶层评论, 占**评论**额度, 记 channel=comment
  2. reply --reply-to N (N>=2) -> 楼中楼, 占**回复**额度, 记 channel=reply
  3. 评论额度满时, 顶层回复被拦; 回复额度满时, 楼中楼被拦
  4. 顶层回复和 comment 命令共用"每帖顶层评论 <= 3"护栏
  5. 账本 channel 与实际形态一致 (重建计数时口径不会漂)
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
    """记录 create_reply 的入参, 不发任何请求。"""

    def __init__(self):
        self.calls = []
        self.top_level = 0  # 服务端视角: 我发过的顶层评论数

    def get_session(self):
        return {"user": {"id": "ME"}}

    def get_topic_posts(self, tid, limit=20, sort=None, cursor=None):
        # 顶层评论护栏 (my_top_level_count) 数的是**服务端楼层**,
        # 所以这里要把我发过的顶层评论作为楼层喂回去, 护栏才看得见。
        items = [
            {
                "id": f"p{i}",
                "postNumber": i + 1,
                "author": {"id": "ME"},
                "replyToPostNumber": 1,
                "createdAt": "2026-11-01T00:00:00.000Z",
            }
            for i in range(self.top_level)
        ]
        return {"items": items, "pageInfo": {"hasNextPage": False}}

    def get_topic(self, tid):
        return {"title": "T", "author": {"id": "ME"}}

    def create_reply(self, tid, body, reply_to_post_number=None, channel=None, **kw):
        self.calls.append({"reply_to": reply_to_post_number, "channel": channel})
        if reply_to_post_number in (None, 1):
            self.top_level += 1
        return {"status": "PUBLISHED", "postId": "x"}


def run_reply(client, tmp, reply_to, daily=None, top_level=None):
    """在隔离的 state 目录里跑一次 cmd_reply, 返回 (是否被拦, client.calls, daily)。"""
    d = Path(tempfile.mkdtemp(dir=tmp))
    B.state_file = lambda base: d / base
    C.state_file = lambda base: d / base
    if top_level is not None:
        client.top_level = top_level
    st = {"daily": {"date": B.today_key(), "comments": 0, "replies": 0}, "watched": {}}
    if daily:
        st["daily"].update(daily)
    B.save_state(st)
    args = types.SimpleNamespace(
        topic_id="t1", reply_to=reply_to, body="x", title="", notify=None
    )
    blocked = False
    try:
        B.cmd_reply(client, args)
    except SystemExit as e:
        blocked = e.code == 3
    after = json.loads((d / ".bot_state.json").read_text(encoding="utf-8"))
    return blocked, list(client.calls), after["daily"]


TMP = tempfile.mkdtemp()
print("=== 测试 1: reply --reply-to 1 算顶层评论, 占评论额度 ===")
c1 = FakeClient()
blocked, calls, daily = run_reply(c1, TMP, 1)
check("没被拦 (额度充足)", not blocked)
check("确实发了顶层评论 (reply_to=1)", calls and calls[0]["reply_to"] == 1, f"calls={calls}")
check("channel 记为 comment", calls and calls[0]["channel"] == "comment", f"calls={calls}")
check("评论计数 +1", daily["comments"] == 1, f"got {daily['comments']}")
check("回复计数不变", daily["replies"] == 0, f"got {daily['replies']}")

print("\n=== 测试 2: reply --reply-to 15 算楼中楼, 占回复额度 ===")
c2 = FakeClient()
blocked, calls, daily = run_reply(c2, TMP, 15)
check("没被拦", not blocked)
check("reply_to 原样传下去", calls and calls[0]["reply_to"] == 15, f"calls={calls}")
check("channel 记为 reply", calls and calls[0]["channel"] == "reply", f"calls={calls}")
check("回复计数 +1", daily["replies"] == 1, f"got {daily['replies']}")
check("评论计数不变", daily["comments"] == 0, f"got {daily['comments']}")

print("\n=== 测试 3: reply_to=0 / None 也算顶层评论 ===")
for rt in (0, None):
    c3 = FakeClient()
    blocked, calls, daily = run_reply(c3, TMP, rt)
    check(f"reply_to={rt!r} -> 记成顶层评论",
          calls and calls[0]["channel"] == "comment", f"calls={calls}")
    check(f"reply_to={rt!r} -> 占评论额度",
          daily["comments"] == 1 and daily["replies"] == 0, f"daily={daily}")

print("\n=== 测试 4: 评论额度满 -> 顶层回复被拦 ===")
c4 = FakeClient()
blocked, calls, daily = run_reply(c4, TMP, 1, daily={"comments": 3, "replies": 0})
check("被护栏拦下", blocked)
check("没有发出任何请求", not calls, f"calls={calls}")
check("计数没变", daily["comments"] == 3 and daily["replies"] == 0, f"daily={daily}")

print("\n=== 测试 5: 评论额度满, 但回复额度还有 -> 楼中楼仍可发 ===")
c5 = FakeClient()
blocked, calls, daily = run_reply(c5, TMP, 15, daily={"comments": 3, "replies": 0})
check("没被拦 (走的是回复额度)", not blocked)
check("发出去了", len(calls) == 1, f"calls={calls}")

print("\n=== 测试 6: 回复额度满 -> 楼中楼被拦 ===")
c6 = FakeClient()
blocked, calls, daily = run_reply(c6, TMP, 15, daily={"comments": 0, "replies": 12})
check("被护栏拦下", blocked)
check("没有发出任何请求", not calls, f"calls={calls}")

print("\n=== 测试 7: 每帖顶层评论 <= 3 护栏对 reply 路径同样生效 ===")
c7 = FakeClient()
blocked, calls, daily = run_reply(c7, TMP, 1, top_level=3)
check("第 4 条顶层评论被拦", blocked)
check("没有发出任何请求", not calls, f"calls={calls}")

print("\n=== 测试 8: 账本 channel 与实际形态一致 (重建计数口径不漂) ===")
rows = [
    {"kind": "reply", "channel": "comment", "replyTo": 1, "at": f"{B.today_key()} 01:00:00"},
    {"kind": "reply", "channel": "reply", "replyTo": 18, "at": f"{B.today_key()} 02:00:00"},
]
orig = C.ledger_read
C.ledger_read = lambda: list(rows)
try:
    lc, lr = B.ledger_daily_counts(B.today_key())
finally:
    C.ledger_read = orig
check("顶层评论计入 comments", lc == 1, f"got {lc}")
check("楼中楼计入 replies", lr == 1, f"got {lr}")

print(f"\n{'=' * 50}")
print(f"通过 {PASSES} / 失败 {FAILS}")
sys.exit(1 if FAILS else 0)
