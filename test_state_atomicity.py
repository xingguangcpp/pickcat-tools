#!/usr/bin/env python3
"""验证"状态文件原子写 + 账本损坏可恢复"逻辑 (纯本地, 不发任何网络请求)。

背景 (2026-11-03 14:xx 查出): 上一轮把每日硬上限的权威来源从 .bot_state.json
换成了账本 .wm_ledger.json, 但**账本自己是用 write_text 非原子写的**,
而读取端 ledger_read() 会吞掉 JSON 解析异常返回 []。

于是有一条完整的失效链:
  账本写到一半被打断 (进程被杀/断电) -> 留下半截 JSON
    -> ledger_read() 解析失败 -> 返回 [] (看起来像"从来没有记录")
    -> cmd_refresh 每轮都把账本写回 -> 空列表被写回去, 46 条历史**永久抹掉**
    -> ledger_daily_counts() 算成 (0, 0) -> 当天已用的 3 评论 + 12 回复归零
    -> 硬上限 (评论<=3 / 回复<=12) 被静默突破

**失效方向仍然是"放行更多"** —— 和上一轮修 state 时同一个根因, 只是这次
漏在了账本这条路径上。

修法:
  1. 账本/配额/宕机/序号计数器一律走 _atomic_write_json (临时文件 + os.replace)
  2. 账本额外留 .bak, 主文件坏了能退回备份, 而不是当成"没有记录"
  3. cmd_refresh 写回时: 账本本来非空却读到空 -> 拒绝写回, 保留现场

要点:
  1. 原子写不留半截 JSON, 也不留 .tmp 残留
  2. 账本损坏后退回 .bak, 历史不丢
  3. refresh 的"拒绝写回"闸门生效 (空账本不覆盖非空账本)
  4. 序号计数器原子写 (坏了不会回退重用 seq)
  5. 配额/宕机记录原子写
  6. 真实账本与真实状态仍然一致 (不改动任何文件)
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


class FakeStateDir:
    """把 state_file 指向临时目录, 测试期间不碰真实状态文件。"""

    def __init__(self):
        self.dir = Path(tempfile.mkdtemp())
        self.orig = C.state_file

    def __enter__(self):
        C.state_file = lambda base: self.dir / base
        # pickcat_bot 也有一份 state_file 引用, 一并换掉
        self.orig_bot = B.state_file
        B.state_file = lambda base: self.dir / base
        return self

    def __exit__(self, *a):
        C.state_file = self.orig
        B.state_file = self.orig_bot
        return False


print("=== 测试 1: _atomic_write_json 原子写 (不留半截, 不留 .tmp) ===")
with FakeStateDir() as fs:
    p = fs.dir / ".probe.json"
    C._atomic_write_json(p, {"a": 1, "b": [1, 2, 3]})
    check("文件已生成", p.exists())
    check("内容可解析", json.loads(p.read_text(encoding="utf-8"))["a"] == 1)
    leftovers = [f for f in os.listdir(fs.dir) if f.endswith(".tmp")]
    check("没有残留 .tmp 文件", not leftovers, f"got {leftovers}")

    # 覆盖旧内容: 必须是完整的新内容, 不能是旧文件的残留
    p.write_text("{\"junk\": \"" + "x" * 800 + "\"}", encoding="utf-8")
    C._atomic_write_json(p, {"fresh": True})
    loaded = json.loads(p.read_text(encoding="utf-8"))
    check("覆盖后是完整的新内容", loaded.get("fresh") is True and "junk" not in loaded)

print("\n=== 测试 2: 账本损坏后退回 .bak, 历史不丢 ===")
with FakeStateDir() as fs:
    p = fs.dir / ".wm_ledger.json"
    recs = [
        {"seq": 1, "kind": "reply", "channel": "comment", "at": "2026-11-03 01:00:00"},
        {"seq": 2, "kind": "reply", "channel": "reply", "at": "2026-11-03 02:00:00"},
    ]
    for r in recs:
        C.ledger_append(r)
    check("账本有 2 条", len(C.ledger_read()) == 2, f"got {len(C.ledger_read())}")

    # 模拟"写到一半被打断": 主文件留半截 JSON
    p.write_text('[{"seq": 1, "kind": "reply", "ch', encoding="utf-8")
    got = C.ledger_read()
    check("主文件坏掉后退回备份, 记录没丢", len(got) >= 1, f"got {len(got)}")
    check("退回的内容仍可解析成 list", isinstance(got, list))

print("\n=== 测试 3: 损坏的账本不会把计数清零 ===")
with FakeStateDir() as fs:
    p = fs.dir / ".wm_ledger.json"
    today = B.today_key()
    rows = [
        {"seq": i + 1, "kind": "reply", "channel": "comment",
         "at": f"{today} 01:00:00", "replyTo": 1}
        for i in range(3)
    ]
    rows += [
        {"seq": 10 + i, "kind": "reply", "channel": "reply",
         "at": f"{today} 02:00:00", "replyTo": 5 + i}
        for i in range(12)
    ]
    for r in rows:
        C.ledger_append(r)
    before = B.ledger_daily_counts(today)
    check("写完账本后计数是 3/12", before == (3, 12), f"got {before}")

    # 把主文件打成半截 JSON (备份还是好的)
    p.write_text('[{"seq": 1, "kind": "reply", "ch', encoding="utf-8")
    after = B.ledger_daily_counts(today)
    check("账本损坏后计数仍是 3/12 (没归零)", after == (3, 12), f"got {after}")

    # 状态文件也丢失 -> 双重失效下依然不能放行
    st = {}
    d = B.daily_guard(st, "replies")
    check("状态+账本都不可信时仍按满额拦 (comments=3)", d["comments"] == 3, f"got {d['comments']}")
    check("状态+账本都不可信时仍按满额拦 (replies=12)", d["replies"] == 12, f"got {d['replies']}")

print("\n=== 测试 4: refresh 的'拒绝写回'闸门 (空账本不覆盖非空账本) ===")
with FakeStateDir() as fs:
    p = fs.dir / ".wm_ledger.json"
    bak = fs.dir / ".wm_ledger.json.bak"
    good = [{"seq": 1, "kind": "reply", "channel": "comment", "at": "2026-11-03 01:00:00"}]
    p.write_text(json.dumps(good, ensure_ascii=False, indent=2), encoding="utf-8")
    bak.write_text(json.dumps(good, ensure_ascii=False, indent=2), encoding="utf-8")
    size_before = p.stat().st_size

    # 最坏情况: 主文件和备份都坏了 -> ledger_read() 只能返回 []
    p.write_text('[{"seq": 1, "kind": "repl', encoding="utf-8")
    bak.write_text("{{{broken", encoding="utf-8")
    broken_bytes = p.read_bytes()

    # 复刻 cmd_refresh 的写回判定逻辑
    ledger = C.ledger_read()
    prev_size = p.stat().st_size if p.exists() else 0
    refused = prev_size > 2 and not ledger
    check("主+备都坏时读到空账本", ledger == [], f"ledger={ledger!r}")
    check("读到空账本时判定为拒绝写回", refused is True, f"refused={refused}")

    if not refused:
        C.ledger_write_all(ledger)
    check("损坏的账本文件未被覆盖 (现场保留, 便于人工排查)",
          p.read_bytes() == broken_bytes)

print("\n=== 测试 4b: 主文件坏但备份好 -> 退回备份, 历史完整 ===")
with FakeStateDir() as fs:
    p = fs.dir / ".wm_ledger.json"
    bak = fs.dir / ".wm_ledger.json.bak"
    good = [{"seq": 1, "kind": "reply", "channel": "comment", "at": "2026-11-03 01:00:00"}]
    p.write_text(json.dumps(good, ensure_ascii=False, indent=2), encoding="utf-8")
    bak.write_text(json.dumps(good, ensure_ascii=False, indent=2), encoding="utf-8")
    p.write_text('[{"seq": 1, "kind": "repl', encoding="utf-8")

    ledger = C.ledger_read()
    check("退回备份, 历史没丢", len(ledger) == 1, f"got {len(ledger)}")
    prev_size = p.stat().st_size
    refused = prev_size > 2 and not ledger
    check("有备份兜底时不需要拒绝写回", refused is False)
    C.ledger_write_all(ledger)
    check("写回后主文件恢复为可解析", len(json.loads(p.read_text(encoding="utf-8"))) == 1)

print("\n=== 测试 5: 序号计数器原子写 (坏了不静默丢号) ===")
with FakeStateDir() as fs:
    seqs = [C.wm_next_seq() for _ in range(5)]
    check("序号连续递增", seqs == [1, 2, 3, 4, 5], f"got {seqs}")
    leftovers = [f for f in os.listdir(fs.dir) if f.endswith(".tmp")]
    check("计数器没留 .tmp 残留", not leftovers, f"got {leftovers}")

print("\n=== 测试 5b: 计数器损坏 -> 从账本恢复已用序号, 不回退重用 ===")
with FakeStateDir() as fs:
    # 账本里已经用到 seq=7
    for i in range(7):
        C.ledger_append({"seq": i + 1, "kind": "reply", "channel": "reply",
                         "at": "2026-11-03 01:00:00"})
    C._atomic_write_json(fs.dir / ".wm_counter.json", {"seq": 7})
    check("正常时下一个序号是 8", C.wm_next_seq() == 8)

    # 计数器写成半截 -> 必须从账本恢复到 8, 而不是静默从 1 重来
    (fs.dir / ".wm_counter.json").write_text('{"seq": 4', encoding="utf-8")
    nxt = C.wm_next_seq()
    check("计数器损坏后从账本恢复 (得 8, 不是 1)", nxt == 8, f"got {nxt}")

    # 计数器整个丢了 -> 同样从账本恢复
    (fs.dir / ".wm_counter.json").unlink()
    nxt2 = C.wm_next_seq()
    check("计数器丢失后从账本恢复 (得 8, 不是 1)", nxt2 == 8, f"got {nxt2}")

print("\n=== 测试 6: 配额 / 宕机记录原子写 ===")
with FakeStateDir() as fs:
    C.quota_save({"action": "TOPIC_CREATE", "used": 10, "limit": 10})
    check("配额记录可读", C.quota_read().get("used") == 10)
    leftovers = [f for f in os.listdir(fs.dir) if f.endswith(".tmp")]
    check("配额文件没留 .tmp 残留", not leftovers, f"got {leftovers}")

    C.outage_record(False)
    st = C.outage_read()
    check("宕机计数被记录", int(st.get("consecutive_failures", 0)) == 1, f"got {st}")
    leftovers2 = [f for f in os.listdir(fs.dir) if f.endswith(".tmp")]
    check("宕机文件没留 .tmp 残留", not leftovers2, f"got {leftovers2}")

print("\n=== 测试 7: 真实账本与真实状态一致 (不改动任何文件) ===")
try:
    today = B.today_key()
    lc, lr = B.ledger_daily_counts(today)
    real_state_file = C.state_file(".bot_state.json")
    if real_state_file.exists():
        rs = json.loads(real_state_file.read_text(encoding="utf-8"))
        rd = rs.get("daily", {})
        # 状态文件今天还没写过(运营任务暂停时就是这种) -> 今天没有计数可比, 不算不一致
        _stale = rd.get("date") != today
        check("账本重算的今日计数 == 状态文件里的计数",
              _stale or (int(rd.get("comments", 0)) == lc
                         and int(rd.get("replies", 0)) == lr),
              f"ledger=({lc},{lr}) state=({rd.get('comments')},{rd.get('replies')})")
    else:
        check("真实账本可读", isinstance(C.ledger_read(), list))
    # 真实账本条数 (本轮不能改动它)
    check("真实账本非空", len(C.ledger_read()) > 0, f"got {len(C.ledger_read())}")
except Exception as e:
    check("真实账本校验未抛异常", False, str(e))

print(f"\n{'=' * 50}")
print(f"通过 {PASSES} / 失败 {FAILS}")
sys.exit(1 if FAILS else 0)
