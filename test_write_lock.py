#!/usr/bin/env python3
"""验证"并发实例不能击穿每日上限"与"账本/序号不会被并发写坏" (纯本地, 不发网络请求)。

背景: 2026-11-03 20:xx 查出 —— 每日上限的判定是"读计数 -> 比较 -> 发请求"
三步, 这���典型的 check-then-act 竞态。**两个进程并发时, 各自读到同一个旧计数,
都会通过护栏**, 于是实际发出的条数超过用户定的硬上限。

实测复现 (修前): 预置当天 11 条回复 (上限 12), 起 4 个并发实例,
**4 条全部发出** —— 当天实际 15 条, 突破 12 的上限。

这��是理论风险: 本项目历史上出现过两次并发实例
(2026-11-01 20:06 / 2026-11-02 21:14), 当时都靠"下一轮先看账本 seq 尾号"
事后发现, 而不是事前拦住。

修法: 把"读计数 -> 判定 -> 记账"整段放进一把**跨进程排他锁** (write_lock)。
  - 进程内: threading.RLock (可重入)
  - 进程间: 独立锁文件 (Windows msvcrt.locking / POSIX fcntl.flock)
  - 拿不到锁 -> 抛异常 (fail closed), 绝不"没锁也硬发"

同时修掉两处**读-改-写**的原始数据竞争:
  - ledger_append(): "读整个账本 -> append -> 整个写回" -> 并发时后写的
    把先写的**整条记录抹掉** -> 账本少一条 = 当天计数少 1 = 上限依据失真。
  - wm_next_seq(): "读计数器 -> +1 -> 写回" -> 并发时两个进程拿到**同一个
    seq** -> 水印编号重复, 审计分不清哪条是哪次调用发的。

要点:
  1. 并发回帖时, 上限只放行到上限为止 (不多发)
  2. 并发评论时, 同上
  3. 账本并发追加不丢记录
  4. 序号并发递增不重复
  5. 锁可重入 (外层已持锁时内层不死锁)
  6. 拿不到锁时抛异常 (fail closed), 不是静默放行
  7. refresh 的写回用"重读 + 只贴增量", 不抹掉并发追加的记录
"""
import json
import sys
import tempfile
import threading
import time
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
B.MAX_TOP_LEVEL = 3               # 被测对象: 需要一个>0 的上限才能放行 1 条
B.MAX_COMMENTS_PER_DAY = 3          # 评论额度: 本测试要验并发放行 1 条

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


TODAY = B.today_key()


class FakeClient:
    """模拟服务端。create_reply 有意加延时, 拉开并发窗口。"""

    def __init__(self, delay=0.05):
        self.created = []
        self.delay = delay
        self._lock = threading.Lock()

    def get_session(self):
        return {"user": {"id": "ME"}}

    def get_topic_posts(self, tid, limit=20, sort=None, cursor=None):
        return {"items": [], "pageInfo": {"hasNextPage": False}}

    def get_topic(self, tid):
        return {"title": "T", "author": {"id": "ME"}}

    def create_reply(self, tid, body, reply_to_post_number=None, channel=None, **kw):
        time.sleep(self.delay)
        with self._lock:
            self.created.append(reply_to_post_number)
            n = len(self.created)
        # **像真实客户端一样往账本追加一条** (pickcat_client.create_reply 就是这么做的)。
        # 不模拟这一步的话, 测出来的账本条数永远不变, 并发丢记录也测不出来。
        C.ledger_append({"seq": n, "kind": "reply",
                         "channel": channel or ("comment"
                                                if reply_to_post_number in (None, 1)
                                                else "reply"),
                         "topicId": tid, "postId": f"new{n}", "replyTo": reply_to_post_number,
                         "status": "PUBLISHED", "at": f"{TODAY} 01:00:00"})
        return {"status": "PUBLISHED", "postId": f"new{n}"}


def use_tmpdir():
    """把 state_file 指向临时目录, 测试期间不碰真实状态文件。"""
    d = Path(tempfile.mkdtemp())
    B.state_file = lambda base: d / base
    C.state_file = lambda base: d / base
    return d


def seed_replies(d, n):
    """预置当天 n 条楼中楼回复 (账本为权威源)。"""
    rows = [{"seq": i, "kind": "reply", "channel": "reply", "topicId": "t",
             "postId": f"p{i}", "replyTo": 5, "status": "PUBLISHED",
             "at": f"{TODAY} 01:00:00"} for i in range(1, n + 1)]
    C._atomic_write_json(d / ".wm_ledger.json", rows)


def run_concurrent_reply(workers=4, pre=None):
    pre = B.MAX_REPLIES_PER_DAY - 1 if pre is None else pre
    """并发起 N 个实例回同一条楼中楼, 返回实际发出条数。"""
    d = use_tmpdir()
    seed_replies(d, pre)
    B.save_state({"daily": {"date": TODAY, "comments": 0, "replies": pre},
                  "watched": {}})
    client = FakeClient()

    def worker():
        try:
            B.cmd_reply(client, types.SimpleNamespace(
                topic_id="t", reply_to=5, body="x", title="", notify=None))
        except SystemExit:
            pass
        except Exception:
            pass

    ts = [threading.Thread(target=worker) for _ in range(workers)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return d, len(client.created)


def run_concurrent_comment(workers=4, pre=2):
    """并发起 N 个实例评同一条顶层评论, 返回实际发出条数。"""
    d = use_tmpdir()
    seed_replies(d, 0)
    B.save_state({"daily": {"date": TODAY, "comments": pre, "replies": 0},
                  "watched": {}})
    client = FakeClient()

    def worker():
        try:
            B.cmd_comment(client, types.SimpleNamespace(
                topic_id="t", body="x", title=""))
        except SystemExit:
            pass
        except Exception:
            pass

    ts = [threading.Thread(target=worker) for _ in range(workers)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return d, len(client.created)


print(f"=== 测试 1: 并发回帖 —— 上限 {B.MAX_REPLIES_PER_DAY}, 预置 {B.MAX_REPLIES_PER_DAY - 1} 条, 4 个并发实例 ===")
d1, sent1 = run_concurrent_reply(workers=4)
check("只放行到上限为止 (最多再发 1 条)", sent1 <= 1, f"实际发出 {sent1} 条")
check("确实发出了那 1 条 (不是全被拦死)", sent1 == 1, f"实际发出 {sent1} 条")
rows1 = json.loads((d1 / ".wm_ledger.json").read_text(encoding="utf-8"))
check(f"账本里当天回复数 = {B.MAX_REPLIES_PER_DAY} ({B.MAX_REPLIES_PER_DAY - 1} 预置 + 1 新发)",
      sum(1 for r in rows1 if r.get("at", "").startswith(TODAY)) == B.MAX_REPLIES_PER_DAY,
      f"got {len(rows1)}")

print("\n=== 测试 2: 并发顶层评论 —— 上限 3, 预置 2 条, 4 个并发实例 ===")
d2, sent2 = run_concurrent_comment(workers=4, pre=max(0, B.MAX_TOP_LEVEL - 1))
check("只放行到上限为止 (最多再发 1 条)", sent2 <= 1, f"实际发出 {sent2} 条")
check("确实发出了那 1 条", sent2 == 1, f"实际发出 {sent2} 条")

print("\n=== 测试 3: 账本并发追加不丢记录 ===")
d3 = use_tmpdir()
C._atomic_write_json(d3 / ".wm_ledger.json", [])
errors = []


def append_worker(i):
    try:
        C.ledger_append({"seq": i, "kind": "reply", "channel": "reply",
                         "topicId": "t", "postId": f"p{i}", "replyTo": 5,
                         "status": "PUBLISHED", "at": f"{TODAY} 01:00:00"})
    except Exception as e:  # 锁拿不到时应该往上抛, 不能静默吞
        errors.append(e)


ts = [threading.Thread(target=append_worker, args=(i,)) for i in range(1, 21)]
for t in ts:
    t.start()
for t in ts:
    t.join()
rows3 = json.loads((d3 / ".wm_ledger.json").read_text(encoding="utf-8"))
check("20 条并发追加全部落盘 (一条都没丢)", len(rows3) == 20, f"got {len(rows3)}")
check("没有重复的 seq", len({r.get("seq") for r in rows3}) == 20,
      f"got {len({r.get('seq') for r in rows3})} 个不同 seq")

print("\n=== 测试 4: 序号并发递增不重复 ===")
d4 = use_tmpdir()
C._atomic_write_json(d4 / ".wm_counter.json", {"seq": 0})
seqs = []
seq_lock = threading.Lock()


def seq_worker():
    try:
        s = C.wm_next_seq()
        with seq_lock:
            seqs.append(s)
    except Exception:
        pass


ts = [threading.Thread(target=seq_worker) for _ in range(20)]
for t in ts:
    t.start()
for t in ts:
    t.join()
check("拿到 20 个序号", len(seqs) == 20, f"got {len(seqs)}")
check("序号互不重复", len(set(seqs)) == len(seqs), f"got {sorted(seqs)}")

print("\n=== 测试 5: 锁可重入 (外层已持锁时内层不死锁) ===")
d5 = use_tmpdir()
ok = True
try:
    with C.write_lock():
        with C.write_lock():
            with C.write_lock():
                pass
except Exception as e:
    ok = False
    print(f"    (嵌套加锁抛异常: {e})")
check("嵌套三层加锁不死锁", ok)

print("\n=== 测试 6: 拿不到锁时抛异常 (fail closed, 不是静默放行) ===")
d6 = use_tmpdir()
orig_acquire = C._acquire_file_lock
raised = False
try:
    def _boom(timeout=60.0):
        raise OSError("模拟锁被别的进程占着, 拿不到")
    C._acquire_file_lock = _boom
    try:
        with C.write_lock():
            pass
    except OSError:
        raised = True
finally:
    C._acquire_file_lock = orig_acquire
check("拿不到锁时 write_lock 抛异常 (调用方可据此放弃写操作)", raised)

print("\n=== 测试 7: refresh 的写回不抹掉并发追加的记录 ===")
d7 = use_tmpdir()
seed_replies(d7, 3)
# 模拟: 复查期间另一个实例追加了一条
C.ledger_append({"seq": 99, "kind": "reply", "channel": "reply", "topicId": "t2",
                  "postId": "pz", "replyTo": 7, "status": "PUBLISHED",
                  "at": f"{TODAY} 02:00:00"})
# 现在走 refresh 的写回路径: 重读 + 只贴增量
with C.write_lock():
    ledger = C.ledger_read()
    C.ledger_write_all(ledger)
rows_after = json.loads((d7 / ".wm_ledger.json").read_text(encoding="utf-8"))
check("并发追加的记录还在 (没被旧快照抹掉)",
      any(r.get("seq") == 99 for r in rows_after),
      f"got {[r.get('seq') for r in rows_after]}")
check("原有 3 条也还在", len(rows_after) == 4, f"got {len(rows_after)}")

print("\n=== 测试 8: 跨进程互斥 (真开子进程, 不是线程) ===")
# 线程测试只证明进程内互斥; 真正要防的是"两个自动化实例同时跑" = 两个**进程**。
# 这里起一个子进程, 让它持锁一段时间, 父进程此时必须拿不到锁。
import subprocess  # noqa: E402

d8 = use_tmpdir()
holder = (
    "import sys, time\n"
    f"sys.path.insert(0, {str(HERE)!r})\n"
    "import pickcat_client as C\n"
    "C.state_file = lambda base: __import__('pathlib').Path(%r) / base\n"
    "with C.write_lock():\n"
    "    print('HELD', flush=True)\n"
    "    time.sleep(3)\n"
    "print('DONE', flush=True)\n" % str(d8)
)
proc = subprocess.Popen([sys.executable, "-c", holder], stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, text=True)
try:
    line = proc.stdout.readline().strip()
    check("子进程已持锁", line == "HELD", f"got {line!r}")
    # 父进程此时拿锁 -> 必须失败 (而不是干等 60 秒或直接放行)
    t0 = time.time()
    failed = False
    try:
        C._acquire_file_lock(timeout=1.0)
    except OSError:
        failed = True
    elapsed = time.time() - t0
    check("父进程拿不到锁 (跨进程互斥生效)", failed)
    check("超时时间合理 (没干等太久)", elapsed < 2.5, f"elapsed={elapsed:.2f}s")
finally:
    proc.wait(timeout=30)

print("\n=== 测试 9: 跨进程并发回帖 —— 真正两个进程, 只应放行 1 条 ===")
d9 = use_tmpdir()
seed_replies(d9, B.MAX_REPLIES_PER_DAY - 1)
B.save_state({"daily": {"date": TODAY, "comments": 0, "replies": B.MAX_REPLIES_PER_DAY - 1}, "watched": {}})
# 两个"实例"各起一个真子进程, 同时回同一条楼中楼。
# 注意: 子进程脚本里不要出现裸的 % (会和外层的字符串格式化打架) ——
# 用 .format() 而不是 % 运算符。
child = (
    "import sys, types, pathlib\n"
    "sys.path.insert(0, {here!r})\n"
    "import pickcat_bot as B, pickcat_client as C\n"
    "d = pathlib.Path({d9!r})\n"
    "B.state_file = lambda base: d / base\n"
    "C.state_file = lambda base: d / base\n"
    "class F:\n"
    "    def get_session(self): return {{'user': {{'id': 'ME'}}}}\n"
    "    def get_topic_posts(self, *a, **k): return {{'items': [], 'pageInfo': {{'hasNextPage': False}}}}\n"
    "    def get_topic(self, t): return {{'title': 'T', 'author': {{'id': 'ME'}}}}\n"
    "    def create_reply(self, tid, body, reply_to_post_number=None, channel=None, **kw):\n"
    "        C.ledger_append({{'seq': 100, 'kind': 'reply', 'channel': 'reply',\n"
    "                        'topicId': tid, 'postId': 'X', 'replyTo': reply_to_post_number,\n"
    "                        'status': 'PUBLISHED', 'at': B.today_key() + ' 01:00:00'}})\n"
    "        print('SENT', flush=True)\n"
    "        return {{'status': 'PUBLISHED', 'postId': 'X'}}\n"
    "try:\n"
    "    B.cmd_reply(F(), types.SimpleNamespace(topic_id='t', reply_to=5, body='x', title='', notify=None))\n"
    "except SystemExit:\n"
    "    print('BLOCKED', flush=True)\n"
).format(here=str(HERE), d9=str(d9))
procs = [subprocess.Popen([sys.executable, "-c", child], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True) for _ in range(2)]
outs = []
errs = []
for p in procs:
    so, se = p.communicate(timeout=60)
    outs.append(so)
    errs.append(se)
sent = sum(1 for o in outs if "SENT" in o)
blocked = sum(1 for o in outs if "BLOCKED" in o)
check("两个进程里只有 1 个发出 (另一个被护栏拦下)", sent == 1, f"sent={sent} blocked={blocked}")
check("另一个确实是被拦下 (不是崩了)", blocked == 1, f"sent={sent} blocked={blocked}")
if blocked != 1:
    # 子进程崩了(而不是被护栏拦下)时把 stderr 打出来, 否则无从查起
    for i, (o, e) in enumerate(zip(outs, errs)):
        if "SENT" not in o and "BLOCKED" not in o:
            print(f"  [子进程 {i} 未产出 SENT/BLOCKED, 退出码异常]")
            print("  stderr:", (e or "").strip()[-800:])

print(f"\n{'=' * 50}")
print(f"通过 {PASSES} / 失败 {FAILS}")
sys.exit(1 if FAILS else 0)
