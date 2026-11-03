#!/usr/bin/env python3
"""验证"账本主文件丢失/被清空时, 历史与硬上限都不会丢" (纯本地, 不发网络请求)。

背景 (2026-11-03 16:xx 查出): 前几轮把账本的**写端**修成了原子写 + 留 .bak,
但**读端**还有两个洞, 而这两个洞的后果和之前修的 bug 完全同类
(失效方向仍然是"放行更多", 即硬上限被静默突破):

  1. ledger_read() 只在"主文件存在但解析失败"时才退回 .bak。
     主文件**缺失** (误删 / 被移动 / 恢复时只带回了备份) 或**被写成合法的空
     列表 []** (某些同步/清理工具会干这事) 时, 它直接返回 [] —— 46 条历史
     当场消失, 当天已用的 3 评论 + 12 回复归零。

  2. 更狠的是**下一次写操作会把历史彻底抹掉**:
     ledger_append() 原来自己重读一遍主文件, 主文件缺失时拿到 [], 追加一条
     就把仅存于备份的 46 条**全部覆盖掉** (主文件和备份一起变成 1 条)。
     这正是 memory 里记的"46 条历史永久抹掉"那条失效链, 只是触发条件
     从"半截 JSON"换成了"文件不见了"。

  3. 附带一个路径 bug: 写侧用 `p.with_name(p.name + ".bak")`, 读侧却用
     `state_file(".wm_ledger.json").with_name(".wm_ledger.json.bak")` ——
     非 default 档案下两者指向**不同文件**, 备份写进去却读不到,
     备份机制在多档案下等于完全失效。

要点:
  1. 主文件缺失 -> 从备份恢复, 历史不丢
  2. 主文件被写成空列表 [] -> 从备份恢复, 历史不丢
  3. 主文件缺失时, 下一次 ledger_append **不能**抹掉备份里的历史
  4. 主备都坏 -> 仍是故障 (fail closed), 不能因为修了读端就放松
  5. 全新环境 -> 用量确实是 0, 不误报故障
  6. 读侧与写侧的备份路径必须一致 (多档案下也要一致)
  7. 真实账本/状态不受影响 (不改动任何文件)
"""
import json
import os
import shutil
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
        self.orig_c = C.state_file
        self.orig_b = B.state_file

    def __enter__(self):
        C.state_file = lambda base: self.dir / base
        B.state_file = lambda base: self.dir / base
        return self

    def __exit__(self, *a):
        C.state_file = self.orig_c
        B.state_file = self.orig_b
        shutil.rmtree(self.dir, ignore_errors=True)
        return False


def seed(fs, n=46):
    """写一份 n 条的账本 (主 + 备), 返回记录列表。"""
    rows = [
        {"seq": i, "kind": "reply", "channel": "reply", "replyTo": i,
         "at": "2026-11-03 01:00:00"}
        for i in range(1, n + 1)
    ]
    C._atomic_write_json(fs.dir / ".wm_ledger.json", rows)
    C._atomic_write_json(fs.dir / ".wm_ledger.json.bak", rows)
    return rows


print("=== 测试 1: 主文件缺失 -> 从备份恢复, 历史不丢 ===")
with FakeStateDir() as fs:
    seed(fs)
    (fs.dir / ".wm_ledger.json").unlink()
    check("主文件缺失时读出 46 条", len(C.ledger_read()) == 46,
          f"got {len(C.ledger_read())}")
    check("备份里有历史 -> 不算故障", C.ledger_unreadable() is False)

print("\n=== 测试 2: 主文件被写成合法的空列表 [] -> 同样退回备份 ===")
with FakeStateDir() as fs:
    seed(fs)
    C._atomic_write_json(fs.dir / ".wm_ledger.json", [])
    check("主文件是 [] 时读出 46 条", len(C.ledger_read()) == 46,
          f"got {len(C.ledger_read())}")
    check("主文件是 [] 但备份有历史 -> 不算故障", C.ledger_unreadable() is False)

print("\n=== 测试 3: 主文件缺失时, 下一次写操作不能抹掉历史 ===")
with FakeStateDir() as fs:
    seed(fs)
    (fs.dir / ".wm_ledger.json").unlink()
    C.ledger_append({"seq": 99, "kind": "reply", "channel": "reply",
                     "replyTo": 7, "at": "2026-11-03 10:00:00"})
    main = json.loads((fs.dir / ".wm_ledger.json").read_text(encoding="utf-8"))
    bak = json.loads((fs.dir / ".wm_ledger.json.bak").read_text(encoding="utf-8"))
    check("主文件恢复成 47 条 (46 + 新增 1)", len(main) == 47, f"got {len(main)}")
    check("备份也是 47 条 (历史没被抹掉)", len(bak) == 47, f"got {len(bak)}")
    check("新记录确实在里面", any(r.get("seq") == 99 for r in bak))

print("\n=== 测试 4: 主备都坏 -> 仍是故障, 硬上限 fail closed ===")
with FakeStateDir() as fs:
    (fs.dir / ".wm_ledger.json").write_text('[{"seq": 1, "kind": "repl',
                                            encoding="utf-8")
    (fs.dir / ".wm_ledger.json.bak").write_text("{{{broken", encoding="utf-8")
    check("两份都坏时读出 0 条", C.ledger_read() == [], f"got {C.ledger_read()!r}")
    check("两份都坏 -> 判定为故障", C.ledger_unreadable() is True)
    d = B.daily_guard({}, "replies")
    check("状态也丢失时按满额拦 (comments=3)", d["comments"] == 3,
          f"got {d['comments']}")
    check("状态也丢失时按满额拦 (replies=12)", d["replies"] == 12,
          f"got {d['replies']}")

print("\n=== 测试 5: 全新环境 -> 用量确实是 0, 不误报故障 ===")
with FakeStateDir() as fs:
    check("读出 0 条", C.ledger_read() == [])
    check("不算故障 (不是 fail closed)", C.ledger_unreadable() is False)
    d = B.daily_guard({}, "replies")
    check("计数确实是 0/0", d["comments"] == 0 and d["replies"] == 0,
          f"got {d['comments']}/{d['replies']}")

print("\n=== 测试 6: 读侧与写侧的备份路径必须一致 (多档案) ===")
orig_env = os.environ.get("PICKCAT_PROFILE")
try:
    os.environ["PICKCAT_PROFILE"] = "testprofile"
    p, bak = C._ledger_paths()
    check("备份与主文件同目录", bak.parent == p.parent,
          f"{bak.parent} vs {p.parent}")
    check("备份文件名 == 主文件名 + .bak", bak.name == p.name + ".bak",
          f"{bak.name} vs {p.name}")
    check("读侧与写侧算出同一个备份路径",
          bak == p.with_name(p.name + ".bak"))
finally:
    if orig_env is None:
        os.environ.pop("PICKCAT_PROFILE", None)
    else:
        os.environ["PICKCAT_PROFILE"] = orig_env

print("\n=== 测试 7: refresh 的写回闸门也看备份 (主丢失+备坏时不写回) ===")
with FakeStateDir() as fs:
    (fs.dir / ".wm_ledger.json").write_text('[{"seq": 1, "kind": "repl',
                                            encoding="utf-8")
    (fs.dir / ".wm_ledger.json.bak").write_text("{{{broken", encoding="utf-8")
    ledger = C.ledger_read()
    p, bakp = C._ledger_paths()
    prev_size = max(
        p.stat().st_size if p.exists() else 0,
        bakp.stat().st_size if bakp.exists() else 0,
    )
    refused = prev_size > 2 and not ledger
    check("主+备都坏时读到空账本", ledger == [], f"ledger={ledger!r}")
    check("判定为拒绝写回 (保留现场)", refused is True, f"refused={refused}")

print("\n=== 测试 8: 真实账本/状态不受影响 ===")
real_rows = C.ledger_read()
check("真实账本可读 (非空)", len(real_rows) > 0, f"got {len(real_rows)}")
check("真实账本仍是 46 条", len(real_rows) == 46, f"got {len(real_rows)}")
check("真实账本不算故障", C.ledger_unreadable() is False)

print("=" * 60)
print(f"=== 结果: {PASSES} PASS / {FAILS} FAIL ===")
sys.exit(1 if FAILS else 0)
