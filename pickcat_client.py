"""
pickcat_client.py — Pickcat 社区(编程猫) 非官方 API 客户端 (Python 标准库版)

重要声明 (务必先读 README.md):
- 这是社区逆向出来的非官方接口, 官方未开放文档, 随时可能失效。
- 使用自动化接口大概率违反平台 ToS。本工具只用于管理"本人账号"内容,
  并内置护栏 (写操作间隔 / dry-run 预览 / 仅限本人 uid)。
- 删除/编辑帖子: 当前已确认的 API 表面里不存在该端点, 本客户端未实现。

已运行时端到端验证 (真实账号 + 真实发帖成功):
  GET  https://pickcat.dao3.fun/api/v1/session              -> 401(无凭证) / 本人信息(有凭证)
  POST https://pickcat.dao3.fun/api/v1/posts               -> 202 异步审核, 后转 PUBLISHED
  GET  https://pickcat.dao3.fun/api/v1/post-submissions/{id} -> 审核状态
  GET  https://pickcat.dao3.fun/api/v1/tags                -> 标签列表
  GET  https://pickcat.dao3.fun/api/v1/users/{uid}/topics  -> 本人帖子列表

⚠️ 主机坑: cdsq.dao3.fun 会把所有请求 302 重定向到 pickcat.dao3.fun。
   用 urllib 时, 302 会把 POST 静默降级成 GET 并丢弃 body, 于是 POST /api/v1/posts
   变成 GET 后返回 404。解决: 默认 base 直接用 pickcat.dao3.fun 直连, 不要走 cdsq。

零依赖: 只用 urllib + http.cookiejar + uuid + json。
"""

import contextlib
import json
import os
import re
import threading
import time
import uuid
import urllib.parse
import urllib.request
import urllib.error
from http.cookiejar import CookieJar
from pathlib import Path

DEFAULT_BASE = "https://pickcat.dao3.fun"
USER_AGENT = "pickcat-tools/0.1 (+self-management)"

# 发帖端点: 文档站 / Rust 库均写 /api/v1/posts, 真实服务器也确实认这个路径。
# 之前遇到的 404 不是路径错, 而是 base 用了 cdsq.dao3.fun 触发 302 重定向
# (urllib 把 POST 降级为 GET) 导致的。直连 pickcat.dao3.fun 即正常。
POST_PATH = "/api/v1/posts"
TAGS_PATH = "/api/v1/tags"

# 社区等级门槛: 实测 level 0 的账号发帖/评论都会被拒
# (403 PARTICIPATION_LEVEL_REQUIRED, details 里给 currentLevel/requiredLevel)。
# 新注册账号是 level 0, 补全资料/完成入驻流程后才会升到 level 1。
PARTICIPATION_LEVEL_REQUIRED = 1

# ---------------------------------------------------------------------------
# 隐形水印: 标记"这条内容是走 API 发的", 并给每次写操作编号
#
# 目的有两个:
#   1) 溯源 —— 任何人都可以扒原始文本看出哪些内容是机器人发的 (可验证的标记,
#      而不是靠"感觉像 AI"); 也方便自己区分"浏览器手发的" vs "API 发的"。
#   2) 计数 —— 序号递增, 能统计调用量, 还能看出哪几次调用"丢了"(缺口)。
#
# 字符选型说明:
#   - 主体用 U+2060 (WORD JOINER) 与 U+2061~U+2064 (不可见算子): 它们语义上
#     就是"不可见", 零宽, 不参与任何排版/emoji 组合, 误报概率极低。
#   - U+200D (ZWJ) 也在字母表里 (按需求), 但它对 emoji 有实义, 所以只允许
#     出现在组内、不放在 emoji 序列中间; 实测是否被服务端清洗由 verify 命令验证。
#   - 不选 U+00AD (软连字符, 换行处会显形) / U+FEFF (BOM, 语义特殊)。
#
# 编码: 6 位 base-6 序号 + 1 位校验和(sum(digits) % 6), 定长 7 字符。
# ---------------------------------------------------------------------------
WM_ALPHABET = ["\u2060", "\u2061", "\u2062", "\u2063", "\u2064", "\u200d"]
WM_WIDTH = 6
# "选中才看得见"的标记: U+200B 零宽空格在多数浏览器里不占位, 但被选中/复制时
# 会显示成一小段空白高亮 —— 读者一选中就能察觉"这段是程序发的", 但肉眼看排版无异。
WM_VISIBLE = "\u200b"
WM_VISIBLE_REPEAT = 2
def _profile():
    """当前账号档案名。

    "default" 沿用旧的顶层状态文件(无需迁移已有数据);
    其他名字各自一份独立状态, 放在 .state/<profile>.<文件名>。
    """
    return os.environ.get("PICKCAT_PROFILE") or "default"


def _read_dotenv():
    cfg = {}
    f = Path(__file__).resolve().parent / ".env"
    if f.exists():
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")
        except Exception:
            pass
    return cfg


def topics_written_today():
    """今天经本工具发过几个主题帖(从账本数)。"""
    import datetime as _dt
    try:
        led = ledger_read()
    except Exception:
        return 0
    today = _dt.date.today()
    n = 0
    for r in led:
        if r.get("kind") != "topic":
            continue
        try:
            d = _dt.datetime.strptime(r.get("at", ""), "%Y-%m-%d %H:%M:%S").date()
        except Exception:
            continue
        if d == today:
            n += 1
    return n


def posting_allowed():
    """写操作总开关。

    社区(编程猫 Pickcat)管理明确禁止"纯 AI 生成的内容"污染论坛, 并对账号执行过
    移除回复 + 禁言处罚。因此这里加一个**默认由 .env 控制的硬闸门**:
    设 `PICKCAT_ALLOW_POSTING=0` 即禁止一切发帖/回帖, 自动化也无法绕过。
    """
    v = os.environ.get("PICKCAT_ALLOW_POSTING")
    if v is None:
        v = _read_dotenv().get("PICKCAT_ALLOW_POSTING")
    return str(v).strip() != "0"


def state_file(base_name):
    """按账号档案解析状态文件路径 (账本/配额/序号/宕机状态都走这里)。"""
    root = Path(__file__).resolve().parent
    p = _profile()
    if p == "default":
        return root / base_name
    d = root / ".state"
    try:
        d.mkdir(exist_ok=True)
    except Exception:
        pass
    return d / f"{p}.{base_name.lstrip('.')}"


# 本地账本: 每次写操作落一条记录。服务端列表接口会截断、也看不到主题首楼,
# 靠列表统计调用量必然漏 —— 账本才是准的。
# 配额状态: 平台对写操作有每日额度 (实测 TOPIC_CREATE 10 个 / 24h 滚动窗)。
# 注意! 配额是**异步失败**的: POST 只返回 202, 失败信息要查投稿审核状态才看得到,
# 所以必须把配额状态落盘, 并在后续写操作前主动拦截, 而不是等它一次次失败。
# 服务可用性状态: 上游 API 挂掉时 (实测 502 Bad Gateway + 10s 超时),
# 前端壳还在但所有 /api/v1/* 取不到数据。没有这个状态的话,
# refresh 会把"服务挂了"误报成"状态无变化", 白白每小时撞一次墙。
# 连续失败多少次判定为"服务不可用"; 判定后冷却 10 分钟内不再重试(避免空转)
# 一次 502 Bad Gateway 已经是确凿信号(不是瞬时抖动), 所以阈值=1;
# 靠冷却期做迟滞: 冷却期内直接跳过, 冷却期过再探一次。
OUTAGE_THRESHOLD = 1
OUTAGE_COOLDOWN = 600


def outage_record(ok):
    """记录一次请求成功/失败, 返回 (是否判定为不可用, 状态字典)。"""
    st = {}
    if state_file(".wm_outage.json").exists():
        try:
            st = json.loads(state_file(".wm_outage.json").read_text(encoding="utf-8"))
        except Exception:
            st = {}
    if ok:
        st = {"down": False, "last_ok_at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "consecutive_failures": 0}
    else:
        st["consecutive_failures"] = int(st.get("consecutive_failures", 0)) + 1
        st["last_fail_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        st["down"] = st["consecutive_failures"] >= OUTAGE_THRESHOLD
    try:
        _atomic_write_json(state_file(".wm_outage.json"), st)
    except Exception:
        pass
    return bool(st.get("down")), st


def outage_read():
    if not state_file(".wm_outage.json").exists():
        return {"down": False, "consecutive_failures": 0}
    try:
        d = json.loads(state_file(".wm_outage.json").read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {"down": False}
    except Exception:
        return {"down": False}


def is_service_down(exc):
    """判断这个异常是不是"服务端不可用"(而不是我们自己的错)。"""
    status = getattr(exc, "status", None)
    if status in (502, 503, 504):
        return True
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        msg = str(body.get("message") or body.get("error") or "")
        if "Bad Gateway" in msg or "Service Unavailable" in msg:
            return True
    txt = str(exc)
    return "Bad Gateway" in txt or "timed out" in txt or "Connection refused" in txt


def service_is_down():
    """当前是否处于"判定不可用且仍在冷却期"状态。"""
    st = outage_read()
    if not st.get("down"):
        return False, st
    last = st.get("last_fail_at")
    if last:
        try:
            t = time.mktime(time.strptime(last, "%Y-%m-%d %H:%M:%S"))
            if time.time() - t > OUTAGE_COOLDOWN:
                return False, st  # 冷却期过了, 允许再探一次
        except Exception:
            pass
    return True, st


def _atomic_write_json(path, obj):
    """原子写 JSON: 先写同目录临时文件, 再 os.replace 覆盖。
    os.replace 在同一文件系统上是原子的 —— 要么是完整的新内容, 要么还是
    旧的完整内容, 不会留半截 JSON。

    为什么所有状态文件都必须这么写: 状态文件一旦被截断, 读取端会吞掉解析
    异常返回默认值, 而**失效方向恰好是"放行更多"**:
      - 账本坏了 -> 每日已用的评论/回复数被当成 0 -> 硬上限失效
      - 配额记录坏了 -> 平台配额闸门失效 -> 继续发到被服务端拒绝
      - 宕机记录坏了 -> 判定不出"服务不可用" -> 反复撞 502
    所以写这些文件的地方一律走这里, 不直接 write_text。
    """
    p = Path(path)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


# ---------------------------------------------------------------------------
# 跨进程写锁 (2026-11-03 20:xx 加)
#
# 问题: 硬上限的判定是"读计数 -> 比较 -> 发请求"三步, 这是典型的 check-then-act
# 竞态。**两个进程并发时, 各自读到同一个旧计数, 都会通过护栏**, 于是实际发出
# 的条数超过用户定的上限。
#
# 实测复现 (2026-11-03 20:xx): 预置当天 11 条回复 (上限 12), 起 4 个并发实例,
# **4 条全部发出** —— 当天实际 15 条回复, 突破硬上限。
#
# 这不是理论风险: 本项目历史上出现过两次并发实例 (11-01 20:06 / 11-02 21:14),
# 当时都是靠"下一轮先看账本 seq 尾号"事后发现, 而不是事前拦住。
#
# 修法: 把"读计数 -> 判定 -> 记账"整段放进一把**跨进程排他锁**里。
#   - 进程内: threading.RLock (可重入 —— 锁的持有者再次进入同一临界区不死锁)
#   - 进程间: 文件锁 (Windows 用 msvcrt.locking, POSIX 用 fcntl.flock),
#     靠**独立的锁文件**实现, 锁的是"锁文件"而不是账本本身 ——
#     这样锁的粒度和被保护的数据是分开的, 不会互相干扰。
#   - 超时: 拿不到锁就**放弃这次写操作** (fail closed)。宁可少回一条,
#     也不能因为等锁超时而"没锁也硬发" —— 那等于把护栏绕开。
# ---------------------------------------------------------------------------

_write_mutex = threading.RLock()      # 进程内互斥 (可重入)
_write_lock_depth = 0                 # 当前进程持有锁的层数 (可重入计数)
_write_lock_fh = None                 # 跨进程锁文件的句柄


def _write_lock_path():
    return state_file(".wm_write.lock")


def _acquire_file_lock(timeout=60.0):
    """跨进程排他锁 (阻塞式, 带超时)。返回句柄, 失败抛 OSError。"""
    import platform

    path = _write_lock_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    if platform.system() == "Windows":
        import msvcrt

        fh = open(path, "a+b")
        # msvcrt.locking 需要至少 1 字节可锁
        fh.seek(0, os.SEEK_END)
        if fh.tell() == 0:
            fh.write(b"\0")
            fh.flush()
        deadline = time.time() + timeout
        while True:
            try:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                return fh
            except OSError:
                if time.time() >= deadline:
                    fh.close()
                    raise
                time.sleep(0.05)
    else:
        import fcntl

        fh = open(path, "a+b")
        deadline = time.time() + timeout
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fh
            except OSError:
                if time.time() >= deadline:
                    fh.close()
                    raise
                time.sleep(0.05)


def _release_file_lock(fh):
    try:
        import platform

        if platform.system() == "Windows":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        fh.close()
    except Exception:
        pass


@contextlib.contextmanager
def write_lock(timeout=60.0):
    """写操作的跨进程排他锁 (可重入)。

    用法::

        with write_lock():
            count = read_count()
            if count < limit:
                do_write()

    这样"读计数 -> 判定 -> 写"整体是原子的, 并发实例会排队, 后一个
    拿到锁时能看到前一个刚写进去的记录, 于是被正确拦下。

    **拿不到锁就抛异常** (调用方应跳过这次写操作, 不要硬发) ——
    失效方向必须是"少发"而不是"多发"。
    """
    global _write_lock_depth, _write_lock_fh
    # 进程内: RLock 可重入, 同一线程嵌套进入不死锁
    _write_mutex.acquire()
    try:
        outermost = _write_lock_depth == 0
        if outermost:
            _write_lock_fh = _acquire_file_lock(timeout)
        _write_lock_depth += 1
        try:
            yield
        finally:
            _write_lock_depth -= 1
            if outermost and _write_lock_fh is not None:
                fh, _write_lock_fh = _write_lock_fh, None
                _release_file_lock(fh)
    finally:
        _write_mutex.release()


def quota_save(record):
    """记录一次配额失败信息 (来自投稿审核结果的 reasonDetails)。"""
    try:
        _atomic_write_json(state_file(".wm_quota.json"), record)
    except Exception:
        pass


def quota_read():
    if not state_file(".wm_quota.json").exists():
        return {}
    try:
        d = json.loads(state_file(".wm_quota.json").read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def quota_remaining():
    """返回距离配额恢复还有多少秒; 0 = 现在就能发; None = 不知道配额。"""
    q = quota_read()
    at = q.get("availableAt")
    if not at:
        return 0
    try:
        import datetime as _dt

        t = _dt.datetime.fromisoformat(at.replace("Z", "+00:00")).timestamp()
        return max(0, int(t - time.time()))
    except Exception:
        return 0


def check_quota(action="TOPIC_CREATE"):
    """写操作前的配额闸门。

    配额是**按动作**分的 (实测 TOPIC_CREATE 有 10/24h 的额度, 评论是另一套),
    所以只拦对应动作 —— 发帖额度用尽时, 回帖/回复还能正常发, 嘴替不至于哑火。
    """
    q = quota_read()
    if not q:
        return
    if q.get("action") and q.get("action") != action:
        return  # 限额的是别的动作, 不影响本次操作
    left = quota_remaining()
    if left <= 0:
        return
    raise PickcatError(
        -2,
        {
            "error": "配额已用尽, 暂停该类写操作",
            "action": q.get("action"),
            "used": q.get("used"),
            "limit": q.get("limit"),
            "availableAt": q.get("availableAt"),
            "retryAfterSeconds": left,
            "hint": "用 `pickcat_bot.py refresh` 复查配额是否已恢复",
        },
    )


def quota_from_submission(record):
    """从投稿审核结果里提取配额信息 (不是 QUOTA_EXCEEDED 就返回 None)。"""
    if not isinstance(record, dict):
        return None
    if record.get("reasonCode") != "QUOTA_EXCEEDED":
        return None
    d = record.get("reasonDetails") or {}
    return {
        "action": d.get("action"),
        "used": d.get("used"),
        "limit": d.get("limit"),
        "windowSeconds": d.get("windowSeconds"),
        "availableAt": d.get("availableAt"),
        "detectedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def ledger_append(record):
    """把一次写操作记进本地账本 (失败不影响主流程)。

    **必须原子写**: 账本是"每日硬上限"的权威兜底来源 (见 pickcat_bot.daily_guard),
    写坏了就等于把当天已用的额度清零。旧的 write_text 写法在写到一半被打断
    (进程被杀/断电) 时会留下半截 JSON, 之后 ledger_read() 解析失败返回 [],
    当天计数直接归零 —— 且失效方向是"放行更多"。

    另外先写 .bak: 账本被写坏时还能从备份恢复历史, 而不是只剩一个空列表。

    **本身是读-改-写, 必须加锁** (2026-11-03 20:xx 修):
    旧实现是"读整个账本 -> append 一条 -> 整个写回"。两个进程同时追加时,
    各自读到同样的旧内容, 后写的把先写的**整条记录抹掉** ——
    账本少一条 = 当天计数少 1 = 硬上限的计数依据失真 (失效方向: 放行更多)。
    所以这里单独加一把跨进程锁。write_lock 是可重入的, 外层已持锁时不会死锁。

    **拿不到锁必须让调用方知道, 不能吞掉**:
    吞掉的话 = 服务端写操作已经成功, 但账本没记上 -> 当天计数少算 1
    -> 硬上限的计数依据失真 (失效方向又是"放行更多")。
    所以锁在 try 外面拿: 拿不到就往上抛, 由调用方决定是否放弃这次写操作。
    """
    p, bak = _ledger_paths()
    with write_lock():
        try:
            # **基于 ledger_read() 的结果追加, 不要自己再读一遍主文件**:
            # 主文件缺失/损坏时, 自己读会拿到 [], 追加一条就把仅存的历史
            # (备份里的 46 条) 覆盖掉 —— 那等于"下一次写操作抹掉全部历史"。
            # ledger_read() 已经统一处理了主备择优。
            data = list(ledger_read())
            data.append(record)
            _atomic_write_json(p, data)
            # 备份写**新内容** (与主文件一致), 不是写之前的老内容。
            # 差别很实在: 备份落后一条的话, 主文件损坏后退回备份就少算了一次写操作
            # —— 当天计数少 1, 上限又松了一点。备份必须与主文件等价。
            try:
                _atomic_write_json(bak, data)
            except Exception:
                pass
        except Exception:
            pass


def ledger_unreadable():
    """账本文件在磁盘上、却读不出内容 -> True (故障, 不是"没有记录")。

    必须区分三种状态:
      - 主文件和备份都不存在 (全新环境) -> 正常, 用量确实是 0
      - 文件存在且能解析 (哪怕是空列表) -> 正常, 用量确实是 0
      - 文件存在但解析失败 (半截 JSON / 不是数组) -> **故障**

    第二种若当成"没有记录", 每日计数会归零 -> 硬上限 (评论<=3 / 回复<=12)
    被静默突破, 失效方向恰好是"放行更多"。所以这里显式报出来。

    判定要看**主备两份**: 只看主文件的话, 主文件缺失但备份里还躺着 46 条
    历史时会被误判成"全新环境" —— 而实际上历史还在, 只是主文件丢了。
    """
    p, bak = _ledger_paths()
    try:
        main, ms = _read_json_list(p)
        backup, bs = _read_json_list(bak)
    except Exception:
        return True
    if ms == "missing" and bs == "missing":
        return False  # 全新环境, 用量确实是 0
    if ms == "ok" or bs == "ok":
        # 至少有一份能解析 -> 记录读得到 (哪怕是空列表) -> 不是故障
        return False
    # 有文件在磁盘上, 却一份都解析不出来 (主坏且无备, 或主备都坏)
    # -> 用量查不到 -> 故障, 让 daily_guard fail closed
    return True


def ledger_write_all(records):
    """整体重写账本 (refresh 更新审核状态时用) —— 主文件与备份都原子写。

    备份同步更新: 否则备份会一直落后于主文件, 真出事时退回备份等于少记录。
    """
    p, bak = _ledger_paths()
    _atomic_write_json(p, records)
    try:
        _atomic_write_json(bak, records)
    except Exception:
        pass


def _ledger_paths():
    """账本主文件与备份文件的路径。

    **读侧与写侧必须用同一个函数算路径** (2026-11-03 16:xx 修):
    原来写侧用 `p.with_name(p.name + ".bak")`, 读侧却用
    `state_file(".wm_ledger.json").with_name(".wm_ledger.json.bak")` ——
    在非 default 档案下两者指向**不同文件**
    (写: .state/bob.wm_ledger.json.bak / 读: .state/.wm_ledger.json.bak),
    于是备份写进去却读不到, 备份机制在多档案下等于完全失效。
    """
    p = state_file(".wm_ledger.json")
    return p, p.with_name(p.name + ".bak")


def _read_json_list(path):
    """读一个 JSON 列表文件 -> (records, status)。

    status 三态, 不能合并成"空/非空":
      - "missing" 文件不存在 (全新环境, 不是故障)
      - "ok"      解析成功 (可能是空列表 —— 那是"确实没有记录")
      - "corrupt" 文件在磁盘上但解析失败 (半截 JSON / 不是数组) —— **故障**
    """
    try:
        if not path.exists():
            return [], "missing"
        raw = path.read_text(encoding="utf-8")
    except Exception:
        return [], "corrupt"
    try:
        data = json.loads(raw)
    except Exception:
        return [], "corrupt"
    if not isinstance(data, list):
        return [], "corrupt"
    return data, "ok"


def ledger_read():
    """读账本记录。

    **必须能容错**: 主文件可能
      - 缺失 (误删 / 被移动 / 恢复时只带回来了备份)
      - 损坏 (写到一半被打断, 留下半截 JSON)
      - 被写成合法的空列表 [] (某些同步/清理工具会干这事)
    这三种情况都退回 .bak, 而不是当成"从来没有记录" ——
    返回 [] 会让每日计数归零 = **硬上限 (评论<=3 / 回复<=12) 被静默突破**,
    而且失效方向恰好是"放行更多"。

    主备取**记录更多**的那一份: 原子写保证两者本应等价, 但主写成功而备写
    失败 (或反过来) 时, 多的一份才是完整的那份。
    """
    p, bak = _ledger_paths()
    main, ms = _read_json_list(p)
    backup, bs = _read_json_list(bak)
    if len(backup) > len(main):
        return backup
    if ms == "ok":
        return main
    if bs == "ok":
        return backup
    return []


def wm_encode(seq):
    """把序号编码成定长隐形水印组 (7 个不可见字符)。"""
    n = int(seq)
    digits = []
    for _ in range(WM_WIDTH):
        digits.append(n % 6)
        n //= 6
    digits.reverse()
    body = "".join(WM_ALPHABET[d] for d in digits)
    return body + WM_ALPHABET[sum(digits) % 6]


def wm_decode(text):
    """从文本里解出所有水印组, 返回 [(seq, start, end), ...]; 无则空列表。"""
    out = []
    i = 0
    n = len(text or "")
    span = WM_WIDTH + 1
    while i + span <= n:
        window = text[i : i + span]
        if all(ch in WM_ALPHABET for ch in window):
            digits = [WM_ALPHABET.index(ch) for ch in window[:WM_WIDTH]]
            if sum(digits) % 6 == WM_ALPHABET.index(window[WM_WIDTH]):
                seq = 0
                for d in digits:
                    seq = seq * 6 + d
                out.append((seq, i, i + span))
                i += span
                continue
        i += 1
    return out


def wm_strip(text):
    """去掉所有水印组 (以及"选中可见"标记), 便于正常显示/对比。"""
    if not text:
        return text
    for _seq, s, e in sorted(wm_decode(text), key=lambda x: -x[1]):
        text = text[:s] + text[e:]
    return text.replace(WM_VISIBLE, "")


# ---- 服务端禁外链: 发帖前自检, 别浪费水印序号 ----
# 服务端对 markdown 里的站外链接直接 422 EXTERNAL_LINK_NOT_ALLOWED
# (field=markdown, host=<域名>)。带点的 API 名 (entity.velocity / getGroupStorage)
# 不算链接, 只有像 timeapi.io / example.com 这种真域名才会被拦。
# 社区内部域名 (pickcat.dao3.fun) 本身允许, 但我们不主动发链接, 一律当外链拦掉最省事。
_URL_RE = re.compile(
    r"(?i)\b(?:https?://|ftp://|www\.)[^\s<>\"')\]]+"
    r"|(?<![\w.@/-])(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+"
    r"(?:com|cn|net|org|io|dev|me|app|xyz|top|info|co|edu|gov|ai|sh|gg|"
    r"vip|site|online|tech|cloud|fun|store|live|space|icu|pro)\b"
)


def find_external_links(text):
    """找出正文里的站外链接/域名, 返回去重后的 host 列表 (找不到返回 [])。

    只用于**发请求前**自检: 服务端 422 会让整条写操作失败并白烧一个水印序号。
    注意别误伤带点的 API 名 (entity.velocity / player.userKey / getGroupStorage):
    上面正则要求 TLD 在白名单里, 这些标识符的"后缀"不在名单内, 因此不匹配。
    """
    if not text:
        return []
    hosts = []
    for m in _URL_RE.finditer(text):
        raw = m.group(0)
        host = re.sub(r"(?i)^(?:https?://|ftp://|www\.)", "", raw)
        host = host.split("/")[0].split("?")[0].split("#")[0].strip(".")
        if not host or "." not in host:
            continue
        if host.lower().endswith("pickcat.dao3.fun"):
            continue  # 社区自己的域名
        if host.lower() not in hosts:
            hosts.append(host.lower())
    return hosts


def wm_apply(text, seq=None, position="end", visible=False):
    """给文本加上水印组。position: 'end'(默认) 或 'start'。

    visible=True 时额外追加 WM_VISIBLE 标记 (零宽空格): 排版上看不见,
    但被选中/复制时会显示出来, 起到"不扒源码也能看出是程序发的"作用。
    """
    if seq is None:
        seq = wm_next_seq()
    group = wm_encode(seq)
    if visible:
        group = group + WM_VISIBLE * WM_VISIBLE_REPEAT
    if position == "start":
        return group + (text or "")
    return (text or "") + group


def wm_next_seq():
    """取下一个写操作序号 (持久化, 跨进程递增)。

    原子写: 序号计数器坏了会**回退重用** —— 已经用过的 seq 再次出现,
    水印审计就分不清"这条是哪次调用发的", 序号缺口统计也会失真。

    计数器损坏时不静默从 1 重新开始, 而是**从账本里已用的最大 seq 恢复**:
    账本记着每次写操作的 seq, 是"用到哪儿了"的权威依据。少了这一步,
    计数器一坏就等于把水印编号从头开始发, 老序号会被重复使用。

    **读-改-写, 必须加锁** (2026-11-03 20:xx 修):
    旧实现是"读计数器 -> +1 -> 写回"。两个进程同时取号会拿到**同一个 seq**,
    水印编号重复 -> 水印审计分不清"这条是哪次调用发的", 序号缺口统计也失真。
    而且重复的 seq 会让账本里出现两条同号记录, 审计对不上。
    """
    with write_lock():
        n = 0
        if state_file(".wm_counter.json").exists():
            try:
                n = int(json.loads(state_file(".wm_counter.json").read_text(encoding="utf-8")).get("seq", 0))
            except Exception:
                n = 0
        if n <= 0:
            # 计数器丢失或损坏 -> 用账本里的最大 seq 兜底
            try:
                for r in ledger_read():
                    n = max(n, int(r.get("seq") or 0))
            except Exception:
                pass
        n += 1
        try:
            _atomic_write_json(state_file(".wm_counter.json"), {"seq": n})
        except Exception:
            pass
        return n


def wm_peek_seq():
    """看一眼下一个序号, 但**不消耗**它 (dry-run 用)。"""
    if state_file(".wm_counter.json").exists():
        try:
            return int(json.loads(state_file(".wm_counter.json").read_text(encoding="utf-8")).get("seq", 0)) + 1
        except Exception:
            return 1
    return 1



def _parse_ns_cookie_file(path):
    """极简解析 Netscape cookies.txt, 提取 name=value 拼成原始 Cookie 串。

    忽略 # 注释行。每行格式:
    domain<TAB>flag<TAB>path<TAB>secure<TAB>expiry<TAB>name<TAB>value
    之所以自己解析而不是直接用 MozillaCookieJar: 后者在请求时对导入的
    host-only cookie 不触发域名匹配 (已知怪癖), 导致注入失败。这里统一走
    已验证可用的 raw-cookie 注入路径, 更可靠。
    """
    pairs = []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 7:
                pairs.append(f"{parts[5]}={parts[6]}")
            else:
                pairs.append(line)  # 兜底: 当作已是 name=value 片段
    return "; ".join(pairs)


class PickcatError(Exception):
    """API 返回非 2xx 时抛出, 携带解析后的响应体。"""

    def __init__(self, status, body):
        self.status = status
        self.body = body
        try:
            self.code = body.get("code") if isinstance(body, dict) else None
            self.message = body.get("message") if isinstance(body, dict) else None
        except Exception:
            self.code = None
            self.message = None
        super().__init__(f"[{status}] {self.code}: {self.message} | {body}")


class PickcatClient:
    def __init__(
        self,
        username=None,
        password=None,
        base=DEFAULT_BASE,
        min_interval=5.0,
        dry_run=False,
        verbose=False,
        cookie=None,
        cookie_file=None,
    ):
        """
        username/password: 密码登录用 (邮箱+密码)。留空则只做无需鉴权的只读请求。
        cookie: 直接传入浏览器复制的 Cookie 头 (绕过人机验证的合规做法, 见 README)。
        cookie_file: Netscape 格式 cookies.txt 路径 (MozillaCookieJar 加载)。
        base: API 根地址 (不含 /api/v1)。
        min_interval: 两次写操作之间的最小间隔秒数 (护栏, 防止刷太快)。
        dry_run: True 时写操作只打印将要发送的请求, 不真正发出。
        """
        self.base = base.rstrip("/")
        self.username = username
        self.password = password
        self.min_interval = float(min_interval)
        self.dry_run = dry_run
        self.verbose = verbose
        self._raw_cookie = cookie
        if cookie_file:
            self._raw_cookie = _parse_ns_cookie_file(cookie_file)
        self._has_cookie = bool(self._raw_cookie)

        if self._raw_cookie:
            # 原始 Cookie 串: 不走 jar, 每条请求手动注入 (避免被 jar 覆盖)
            self._opener = urllib.request.build_opener()
        else:
            jar = CookieJar()
            self._opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(jar)
            )
        self._last_write = 0.0
        self._session_user_id = None  # 登录后缓存本人 uid
        # 最近一次请求的响应头 (小写 key -> 值)。巡检器用它盯"服务端开始发
        # ETag / 开始限流"这类**协议层**变化。只记录、不参与任何控制逻辑。
        self._last_headers = {}

    # ---------- 底层请求 ----------
    def _request(self, method, path, json_body=None, params=None, extra_headers=None):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        data = json.dumps(json_body).encode("utf-8") if json_body is not None else None
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Origin": self.base,  # 源码里 origin 设为 base_url
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        if extra_headers:
            headers.update(extra_headers)
        if self._raw_cookie:
            headers["Cookie"] = self._raw_cookie

        if self.verbose:
            print(f"[req] {method} {url}")
            if data is not None:
                print(f"[req-body] {data.decode('utf-8')[:500]}")

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener.open(req, timeout=30) as r:
                raw = r.read().decode("utf-8", "replace")
                self._last_headers = {k.lower(): v for k, v in r.headers.items()}
            outage_record(True)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                body = json.loads(raw) if raw else {}
            except Exception:
                body = {"raw": raw}
            err = PickcatError(e.code, body)
            # 错误响应也有头 —— 限流头 (Retry-After / X-RateLimit-*) 恰恰只在
            # 错误响应上出现, 不记就永远看不到服务端开始限流。
            try:
                self._last_headers = {k.lower(): v for k, v in (e.headers or {}).items()}
            except Exception:
                self._last_headers = {}
            if is_service_down(err):
                outage_record(False)
            raise err
        except Exception as e:  # 网络层错误
            err = PickcatError(-1, {"error": str(e)})
            if is_service_down(err):
                outage_record(False)
            raise err

        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return {"raw": raw}

    def _throttle(self):
        """写操作前强制间隔 (护栏)。"""
        now = time.time()
        wait = self.min_interval - (now - self._last_write)
        if wait > 0:
            if self.verbose:
                print(f"[throttle] 等待 {wait:.1f}s 避免过快")
            time.sleep(wait)
        self._last_write = time.time()

    # ---------- 鉴权 ----------
    def login(self):
        """POST /api/v1/session {username, password}。成功后 cookie 自动保存。"""
        if not self.username or not self.password:
            raise PickcatError(-1, {"error": "未提供 username/password"})
        self._request(
            "POST",
            "/api/v1/session",
            json_body={"username": self.username, "password": self.password},
        )
        # 不解析响应, 直接取 session 验证
        return self.get_session()

    def ensure_session(self):
        """获取当前会话: cookie 模式直接取 (无需再登录), 密码模式先登录。"""
        if self._has_cookie:
            return self.get_session()
        return self.login()

    def get_session(self):
        """GET /api/v1/session -> 当前登录用户信息。"""
        ses = self._request("GET", "/api/v1/session")
        if isinstance(ses, dict) and "user" in ses:
            self._session_user_id = ses["user"].get("id")
        return ses

    # ---------- 护栏: 仅限本人 ----------
    def _assert_self(self, user_id):
        if self._session_user_id is None:
            try:
                self.get_session()
            except Exception:
                pass
        if self._session_user_id and user_id != self._session_user_id:
            raise PickcatError(
                -1,
                {
                    "error": "护栏拦截: 目标 uid 与登录账号不一致",
                    "target": user_id,
                    "self": self._session_user_id,
                },
            )

    # ---------- 只读: 列本人帖子 ----------
    def list_user_topics(self, user_id, limit=20):
        """GET /api/v1/users/{uid}/topics (公开, 仅列本人时做护栏校验)。"""
        self._assert_self(user_id)
        return self._request(
            "GET", f"/api/v1/users/{user_id}/topics", params={"limit": limit}
        )

    def list_user_posts(self, user_id, role="reply", limit=20):
        """GET /api/v1/users/{uid}/posts?role=reply (公开)。"""
        self._assert_self(user_id)
        return self._request(
            "GET",
            f"/api/v1/users/{user_id}/posts",
            params={"role": role, "limit": limit},
        )

    def get_profile(self, user_id=None):
        """GET /api/v1/users/{uid} -> 用户资料, 含 level.current 与 stats。

        这是判断"能不能发"的权威来源(比撞 403 再看报错快)。
        """
        if user_id is None:
            user_id = self._session_user_id or (self.get_session().get("user") or {}).get("id")
        return self._request("GET", f"/api/v1/users/{user_id}")

    def get_my_level(self):
        """当前账号的社区等级 (int)。取不到时返回 None。"""
        try:
            prof = self.get_profile()
            lvl = prof.get("level") or {}
            if isinstance(lvl, dict):
                return lvl.get("current")
            return lvl
        except Exception:
            return None

    def check_participation(self, required=PARTICIPATION_LEVEL_REQUIRED):
        """写操作前的等级闸门: level 0 直接抛错, 别浪费一次调用去换 403。"""
        lvl = self.get_my_level()
        if lvl is None or lvl >= required:
            return lvl
        raise PickcatError(
            -3,
            {
                "error": f"社区等级不足: 当前 {lvl}, 需要 {required}",
                "currentLevel": lvl,
                "requiredLevel": required,
                "hint": "新号需先完成社区的入驻流程(在网页版看看等级/任务提示)升到 level 1",
            },
        )

    def get_topic(self, topic_id):
        return self._request("GET", f"/api/v1/topics/{topic_id}")

    def get_topic_posts(self, topic_id, limit=20, sort=None, cursor=None):
        params = {"limit": limit}
        if sort:
            params["sort"] = sort
        if cursor:
            params["cursor"] = cursor
        return self._request("GET", f"/api/v1/topics/{topic_id}/posts", params=params)

    # ---------- 写: 发帖 (带 Idempotency-Key 护栏) ----------
    def list_tags(self):
        """GET /api/v1/tags -> 可发帖用的标签列表 (每项含 id / name 等)。"""
        return self._request("GET", TAGS_PATH)

    # ---------- 护栏: 正文不得含站外链接 ----------
    @staticmethod
    def _assert_no_external_link(markdown):
        """服务端对站外链接直接 422 EXTERNAL_LINK_NOT_ALLOWED, 会白烧一个水印序号。
        所以在**消耗序号之前**自检并拦下来, 附带告诉调用方该怎么改写。"""
        hosts = find_external_links(markdown)
        if hosts:
            raise PickcatError(
                -1,
                {
                    "error": "护栏拦截: 正文含站外链接, 服务端会 422 EXTERNAL_LINK_NOT_ALLOWED",
                    "hosts": hosts,
                    "hint": "把外链改成纯文字描述 (只写项目名, 不写域名/URL), 或改用社区内已有的说法。",
                },
            )

    def create_topic(self, title, markdown, kind="DISCUSSION", tag_ids=None,
                     watermark=True, wm_position="end"):
        """
        POST /api/v1/posts 发布主题首楼 (字段名 camelCase, 与文档站一致)。
        需要 Idempotency-Key + Origin 头 (自动加)。返回 202 异步审核。
        服务端校验: tagIds 至少 1 个 (TOO_FEW, min:1), 否则发帖被拒。
        kind 取值: DISCUSSION / QUESTION / ANNOUNCEMENT。
        watermark: 默认在正文末尾追加隐形水印组 (标记"这条是 API 发的" + 写操作序号)。
        """
        tag_ids = tag_ids or []
        if not self.dry_run:
            if not posting_allowed():
                raise PickcatError(-4, {
                    "error": "写操作已被 PICKCAT_ALLOW_POSTING=0 关闭",
                    "hint": "确认社区规则与本人意愿后再把 .env 里这一项改回 1",
                })
            self.check_participation()  # 等级闸门 (必须在消耗水印序号之前)
            check_quota("TOPIC_CREATE")  # 配额闸门: 发帖额度用尽就别浪费调用
            cap = int(_read_dotenv().get("PICKCAT_BOT_MAX_TOPICS_PER_DAY", "0")
                      or os.environ.get("PICKCAT_BOT_MAX_TOPICS_PER_DAY", "0") or 0)
            if cap and topics_written_today() >= cap:
                raise PickcatError(-4, {
                    "error": f"今日主题帖已达机器人上限 {cap} 条(为号主留额度), 跳过",
                    "hint": "发帖是最显眼的一类, 默认上限 0; 确实要发请人工改 .env",
                })
            self._assert_no_external_link(markdown)
            self._assert_no_external_link(title)
        if not tag_ids and not self.dry_run:
            raise PickcatError(
                -1,
                {
                    "error": "至少需要 1 个 tagId (服务端限制 min:1)。"
                    "先用 list_tags()/CLI `tags` 命令取真实标签 UUID, 再传入。"
                },
            )
        body = {
            "title": title,
            "markdown": markdown,
            "kind": kind,
            "tagIds": tag_ids,
        }
        if watermark:
            # 标题与正文共用同一个序号 (一次写操作 = 一个 seq)
            # dry-run 只预览, 不消耗序号 (否则账本会出现假缺口)
            seq = wm_next_seq() if not self.dry_run else wm_peek_seq()
            body["markdown"] = wm_apply(body["markdown"], seq, position=wm_position, visible=True)
            body["title"] = wm_apply(body["title"], seq, position="end", visible=False)
        key = str(uuid.uuid4())
        if self.dry_run:
            print(
                f"[dry-run] 将 POST {self.base}/api/v1/posts\n"
                f"  Idempotency-Key: {key}\n"
                f"  body: {json.dumps(body, ensure_ascii=False)[:600]}"
            )
            return {"dry_run": True, "idempotency_key": key, "body": body}
        self._throttle()
        resp = self._request(
            "POST",
            POST_PATH,
            json_body=body,
            extra_headers={"Idempotency-Key": key},
        )
        if watermark and isinstance(resp, dict):
            ledger_append(
                {
                    "seq": seq,
                    "kind": "topic",
                    "topicId": resp.get("topicId"),
                    "postId": resp.get("postId"),
                    "submissionId": resp.get("submissionId"),
                    "status": resp.get("status"),
                    "title": wm_strip(body.get("title", "")),
                    "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
        return resp

    def create_reply(self, topic_id, markdown, reply_to_post_number=None,
                     watermark=True, wm_position="end", channel=None, react=None):
        """POST /api/v1/posts 发布回帖 (可楼中楼)。默认追加隐形水印组 + 选中可见标记。

        channel: "comment" (顶层评论) / "reply" (楼中楼回复)。写进账本,
        让"今天发了几条顶层评论、几条楼中楼"可以精确统计 —— 每日上限是硬
        规矩, 计数不能靠猜。

        react: True=回应"有人回我", False=我自选话题去答, None=未知。
        写进账本, 让"主动式用了多少额度"也能从账本重建 (与 daily_guard
        同源: 账本是权威, state 只是缓存)。
        """
        body = {"topicId": topic_id, "markdown": markdown}
        if reply_to_post_number is not None:
            body["replyToPostNumber"] = reply_to_post_number
        if not self.dry_run:
            if not posting_allowed():
                raise PickcatError(-4, {
                    "error": "写操作已被 PICKCAT_ALLOW_POSTING=0 关闭",
                    "hint": "确认社区规则与本人意愿后再把 .env 里这一项改回 1",
                })
            self.check_participation()  # 等级闸门
            check_quota("TOPIC_REPLY")  # 配额闸门 (按动作分: 评论额度独立)
            self._assert_no_external_link(markdown)  # 站外链接闸门 (在消耗序号之前)
        seq = None
        if watermark:
            seq = wm_next_seq() if not self.dry_run else wm_peek_seq()
            body["markdown"] = wm_apply(body["markdown"], seq, position=wm_position, visible=True)
        key = str(uuid.uuid4())
        if self.dry_run:
            print(
                f"[dry-run] 将 POST {self.base}/api/v1/posts (reply to {topic_id})\n"
                f"  Idempotency-Key: {key}\n"
                f"  body: {json.dumps(body, ensure_ascii=False)[:600]}"
            )
            return {"dry_run": True, "idempotency_key": key, "body": body}
        self._throttle()
        resp = self._request(
            "POST",
            POST_PATH,
            json_body=body,
            extra_headers={"Idempotency-Key": key},
        )
        if watermark and isinstance(resp, dict):
            eff_channel = channel or ("comment" if reply_to_post_number in (None, 1) else "reply")
            rec = {
                "seq": seq,
                "kind": "reply",
                "channel": eff_channel,
                "topicId": topic_id,
                "postId": resp.get("postId"),
                "submissionId": resp.get("submissionId"),
                "status": resp.get("status"),
                "replyTo": reply_to_post_number,
                "text": wm_strip(markdown)[:80],
                "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            # react 显式写出来 (True/False/None 三态), 这样重建"主动式用量"
            # 时能区分"确实是主动式"和"判不准"。楼中楼回复必须写;
            # 顶层评论不参与回复池的竞争, 不写这个字段。
            if eff_channel == "reply":
                rec["react"] = react
            ledger_append(rec)
        return resp

    # ---------- 写后查询: 审核状态 ----------
    def list_post_submissions(self, status=None, topic_id=None, limit=20):
        params = {"limit": limit}
        if status:
            params["status"] = status
        if topic_id:
            params["topicId"] = topic_id
        return self._request("GET", "/api/v1/post-submissions", params=params)

    def get_post_submission(self, submission_id):
        return self._request("GET", f"/api/v1/post-submissions/{submission_id}")
