#!/usr/bin/env python3
"""
pickcat_bot.py — Pickcat 社区半自动运营助手 (由 WorkBuddy 定时任务驱动)

背景: 社区是编程猫的 Pickcat, 非官方 API 见 README.md。
本脚本把"嘴替"这件事工程化 —— 机器负责盯、判、护栏; LLM 负责写句子。

命令:
  check                       扫描 (只读, 不改状态):
                                 ① 谁回了我 -> 待回复列表
                                 ② feed 里适合评的 AI 帖候选
  reply   --topic-id --reply-to N --body S   回一句 (楼中楼不限; 顶层评论受"每帖<=3"护栏)
  comment --topic-id --body S                评一句 (等价于顶层回复, 同样受护栏)
  watch   --topic-id [--last-seen N]         把帖子加入关注列表 (以后有人回我就跟进)
  state                                    打印当前状态 (关注列表 + 今日计数)

护栏 (硬编码, 不靠自觉):
  - 每帖顶层评论 <= 3 (只数我自己发的), 楼中楼回复不限
  - 每日评论/回复上限 (可用环境变量调)
  - 写操作最小间隔
  - 只动自己账号, 绝不碰别人的帖子 (除"回复别人"外)

凭证: 从本目录 .env 的 PICKCAT_COOKIE 读, 任何输出都不打印它。
状态: .bot_state.json (已 gitignore)
"""

import argparse
import datetime
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from pickcat_client import (  # noqa: E402
    PickcatClient,
    PickcatError,
    outage_record,
    state_file,
    write_lock,
)

ENV_FILE = HERE / ".env"

# 每帖顶层评论上限 (用户定的规矩)
# 每帖顶层评论从 3 降到 1 —— 少插嘴。
# (每日上限等参数在下面 load_env() 之后统一读取, 因为要支持 .env 配置)
# feed 里最多看多少帖用于找 AI 候选 (服务端 feed limit 上限 50, 用 100 直接 400)
FEED_SCAN = int(os.environ.get("PICKCAT_BOT_FEED_SCAN", "50"))
# 最多给几个候选
MAX_CANDIDATES = 5

# 注意: 用 (?<![A-Za-z0-9]) 而不是 \b —— Python 的 \b 在中文里失效,
# 像"大AI时代""用AI做"这种中文紧邻的写法匹配不到 (大 和 A 都是 \w, 中间没有边界)。
# 但也不能去掉前视断言, 否则 "email"/"said" 里的 ai 会误命中。
# 语录来源: 本人主号 —— 只有本人**手写**的旧回复才算语录。
# 带隐形水印的 = 当时是 AI 写的, 不能进语录库: 那等于把被处罚过的内容换个号重新上架。
QUOTE_UID = "01a0d338-72f2-731e-a0c5-10724fc89813"

AI_RE = re.compile(
    r"(?<![A-Za-z0-9])(ai|agi|llm|gpt|agent)(?![A-Za-z0-9])|大模型|智能体|提示词|prompt|"
    r"深度学习|神经网络|机器学习|openai|claude|deepseek|chatbot|自动化|机器人|豆包|"
    # 社区对同一件事的本地叫法 —— 匹配不到这些, [AI 帖候选] 会长期为 0。
    # boxplay 是社区的 AI 搬砖平台 (PikAgent), "boxplay" 才是这里最常出现的词。
    r"boxplay|pikagent|pik\s*agent|kn界",
    re.I,
)


# ---------------------------------------------------------------- 基础工具
def load_env(path=ENV_FILE):
    cfg = {}
    if not path.exists():
        return cfg
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


# 参数来源优先级: 进程环境变量 > .env > 默认值。
# 之前这里只读 os.environ, 结果 .env 里写的上限根本不生效(一直用的是硬编码默认值),
# 已修 —— 改 .env 现在真的管用。
_ENV = load_env()


def _num(key, default, cast=int):
    """取数值配置: 进程环境变量 > 账号档案的 .env > 根 .env > 默认值。

    走 pickcat_client._read_dotenv() 是因为那边做了**档案分层**(accounts/<名字>.env 优先),
    这样主号和小号可以各有一套上限与开关。
    """
    from pickcat_client import _read_dotenv as _layered
    v = os.environ.get(key)
    if v is None:
        v = _layered().get(key)
    if v is None or str(v).strip() == "":
        return default
    try:
        return cast(v)
    except Exception:
        return default


def reload_limits():
    """档案切换后**重新解析**上限。

    这些常量在 import 时求值, 而 --account 是在 main() 里之后才设的 ——
    不重新解析的话, 小号档案自己的 .env 里的上限/开关不会生效。
    """
    global MAX_COMMENTS_PER_DAY, MAX_REPLIES_PER_DAY, MAX_TOP_LEVEL
    global MAX_REPLIES_PER_TOPIC, MAX_PROACTIVE_PER_DAY, MAX_TOPICS_PER_DAY
    global MAX_WRITES_PER_HOUR, WRITE_INTERVAL, OWNER_ACTIVITY_SOFT_CAP
    global REACTIVE_RESERVE, MAX_PROACTIVE_REPLIES_PER_DAY
    global PLATFORM_REPLY_LIMIT_PER_DAY, PLATFORM_TOPIC_LIMIT_PER_DAY
    MAX_COMMENTS_PER_DAY = _num("PICKCAT_BOT_MAX_COMMENTS_PER_DAY", 0)
    MAX_REPLIES_PER_DAY = _num("PICKCAT_BOT_MAX_REPLIES_PER_DAY", 10)
    MAX_TOP_LEVEL = _num("PICKCAT_BOT_MAX_TOP_LEVEL", 0)
    MAX_REPLIES_PER_TOPIC = _num("PICKCAT_BOT_MAX_REPLIES_PER_TOPIC", 1)
    MAX_TOPICS_PER_DAY = _num("PICKCAT_BOT_MAX_TOPICS_PER_DAY", 2)
    MAX_PROACTIVE_PER_DAY = _num("PICKCAT_BOT_MAX_PROACTIVE_PER_DAY", 0)
    MAX_WRITES_PER_HOUR = _num("PICKCAT_BOT_MAX_WRITES_PER_HOUR", 2)
    WRITE_INTERVAL = _num("PICKCAT_BOT_WRITE_INTERVAL", 30.0, float)
    OWNER_ACTIVITY_SOFT_CAP = _num("PICKCAT_BOT_OWNER_SOFT_CAP", 3)
    REACTIVE_RESERVE = _num("PICKCAT_BOT_REACTIVE_RESERVE", 10)
    PLATFORM_REPLY_LIMIT_PER_DAY = _num("PICKCAT_PLATFORM_REPLY_LIMIT", 50)
    PLATFORM_TOPIC_LIMIT_PER_DAY = _num("PICKCAT_PLATFORM_TOPIC_LIMIT", 10)
    MAX_PROACTIVE_REPLIES_PER_DAY = max(0, MAX_REPLIES_PER_DAY - REACTIVE_RESERVE)

# import 时先按根 .env 初始化一份; 档案切换后 main() 会再调 reload_limits() 重解析。
reload_limits()

# "回我"的历史楼层最多回溯多少天 (再早的回信不该翻出来回)
REPLY_HORIZON_DAYS = _num("PICKCAT_BOT_REPLY_HORIZON_DAYS", 7)
# --- 连发回答的合并阈值 ---
# 对方经常把一条长回答拆成连续两三楼发: 同作者、同 replyToPostNumber、
# 楼层号与时间都挨着。语义上这是"一条回答", 不是"几个独立问题"。
# 判据: 同一 (作者, 父楼层) 分组内, 相邻两楼的楼层号差 <= BURST_MAX_GAP_FLOORS
# 且时间差 <= BURST_WINDOW_SEC, 就归为同一条连发回答。
# 楼层号差默认取 1 (严格相邻) —— 宁可漏合并, 也不要把"隔了一楼、真有问题"
# 的楼层误判成已回, 那等于把真问题藏起来, 比重复作答更糟。
BURST_MAX_GAP_FLOORS = _num("PICKCAT_BOT_BURST_MAX_GAP_FLOORS", 1)
BURST_WINDOW_SEC = _num("PICKCAT_BOT_BURST_WINDOW_SEC", 300, float)


def build_client():
    env = load_env()
    # 多账号: 非 default 档案优先读 accounts/<profile>.env (每个号一份 cookie)
    prof = os.environ.get("PICKCAT_PROFILE") or "default"
    if prof != "default":
        acct_file = HERE / "accounts" / f"{prof}.env"
        if acct_file.exists():
            env = {**env, **load_env(acct_file)}
    cookie = env.get("PICKCAT_COOKIE") or os.environ.get("PICKCAT_COOKIE")
    if not cookie:
        print(
            "缺少凭证: 请在 pickcat-tools/.env 里写 PICKCAT_COOKIE=<整串 cookie>",
            file=sys.stderr,
        )
        sys.exit(2)
    base = env.get("PICKCAT_BASE") or os.environ.get("PICKCAT_BASE")
    return PickcatClient(
        cookie=cookie, base=base, min_interval=WRITE_INTERVAL, verbose=False
    )


def load_state():
    if state_file(".bot_state.json").exists():
        try:
            return json.loads(state_file(".bot_state.json").read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(st):
    """原子写状态文件: 先写临时文件, 再 os.replace 覆盖。

    原来直接 write_text, 写到一半被打断 (进程被杀/断电) 就会留下一个
    半截 JSON。下次 load_state() 解析失败 -> 吞异常返回 {} -> 当天计数
    归零 -> 每日上限被静默清零 (详见 daily_guard 的注释)。
    os.replace 在同一文件系统上是原子的, 所以要么完整, 要么还是旧的。
    """
    p = state_file(".bot_state.json")
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def today_key():
    return time.strftime("%Y-%m-%d")


def strip_html(s):
    return re.sub(r"<[^>]+>", "", s or "").strip()


def client_uid(c):
    ses = c.get_session()
    return (ses.get("user") or {}).get("id")


def my_topics(c, uid, limit=30):
    data = c.list_user_topics(uid, limit=limit)
    return (data.get("items") if isinstance(data, dict) else data) or []


def posts_of_full(c, topic_id, limit=100, max_pages=20):
    """返回 (楼层列表, 是否取全)。

    注意: GET /api/v1/topics/{id}/posts 返回的 items 只有顶层回复,
    楼中楼藏在每层的 children 数组里 (replyToPostNumber 指向父楼层)。
    不摊平 children 会漏掉"别人回复了我在楼中楼里的话"这种最典型的回帖。

    **必须翻页**: 服务端 limit 上限 100, 超了要按 pageInfo.nextCursor 续取。
    早期版本固定 limit=50 且不翻页 —— 楼层超过 50 的帖(BoxPlay 帖已 54 楼)
    后面的楼层完全看不见, 其中包含真实"回我"的楼, 会被静默漏掉。

    **第二个返回值"是否取全"是这个函数存在的意义**: 旧版 posts_of 遇
    PickcatError 直接 break, 返回一个**残缺**的列表, 而调用方无从得知 ——
    于是"我在这帖发过几条顶层评论"被少算, "每帖顶层评论<=3"的护栏被绕过
    (失效方向又是"放行更多")。现在把"取全了没有"显式交出去, 让护栏能
    按"查不到 = 按已满处理"来兜底。
    """
    out = []
    seen = set()
    cursor = None
    complete = True
    for _ in range(max_pages):
        try:
            data = c.get_topic_posts(topic_id, limit=limit, cursor=cursor)
        except PickcatError:
            complete = False
            break
        items = (data.get("items") if isinstance(data, dict) else data) or []
        for it in flatten_posts(items):
            key = it.get("id") or it.get("postNumber")
            if key in seen:
                continue
            seen.add(key)
            out.append(it)
        pi = data.get("pageInfo") or {}
        cursor = pi.get("nextCursor")
        if not pi.get("hasNextPage") or not cursor:
            break
    else:
        # 翻页次数用尽但服务端还说"还有下一页" -> 没取全
        complete = False
    return out, complete


def posts_of(c, topic_id, limit=100, max_pages=20):
    """返回该帖所有楼层(含楼中楼), 自动翻页取全 (只要列表, 不关心是否取全)。"""
    return posts_of_full(c, topic_id, limit=limit, max_pages=max_pages)[0]


def flatten_posts(items, _depth=0):
    """递归摊平 items + children, 返回带 postNumber/replyToPostNumber 的扁平列表。"""
    out = []
    if _depth > 6:  # 防异常的深嵌套
        return out
    for it in items or []:
        out.append(it)
        kids = it.get("children") or []
        if kids:
            out.extend(flatten_posts(kids, _depth + 1))
    return out


def max_post_number(c, topic_id):
    items = posts_of(c, topic_id)
    return max([int(i.get("postNumber") or 0) for i in items] or [0])


def _advance_last_seen(c, topic_id, watched):
    """发帖/回帖后推进该帖的"已处理楼层水位"。

    **别直接写 max_post_number**: 写操作是异步审核的 (POST 返回 202,
    楼层要过审才出现在服务端)。刚发完立刻去查楼层, 我的新楼层还没上线,
    于是查到的是**发帖前**的水位 —— 对一个全新的帖子就是 0。

    水位记成 0 的后果: 下一轮 check 里的 `pn <= last` 过滤完全失效,
    这个帖子的所有楼层都会被当成"新楼层"重新冒出来刷屏 (BoxPlay 帖 60 楼
    那种噪声就是这么来的)。不误判回我, 但每轮都要重新读一遍, 白耗。

    修法: 取 max(服务端当前最大楼层, 发帖前的水位 + 1) ——
    我这一层至少会落在"发帖前水位 + 1"这个位置, 就算它还没过审上线,
    水位也不会退回到 0。
    """
    try:
        server_max = max_post_number(c, topic_id)
    except PickcatError:
        server_max = 0
    prev = int((watched.get(topic_id) or {}).get("last_seen") or 0)
    return max(server_max, prev + 1)


def ledger_top_level_records(topic_id):
    """账本里我在该帖发过的顶层评论记录 (每条一个 dict)。

    顶层 = 写操作时 replyTo 指向首楼(1)或为空。`kind="topic"` 的记录是我自己
    开的帖, 不算"在某帖下的评论" (服务端楼层列表也不含首楼, 口径一致)。

    审核失败 (PUBLICATION_FAILED) 的记录不算 —— 那条评论根本没发出去。

    **逐条单独 try**: 一条坏记录 (replyTo 写成非数字之类) 不能让整批归零,
    否则"我回过几个楼层"被清成 0 -> 下一轮重复作答白花回复额度。
    """
    from pickcat_client import ledger_read

    out = []
    for rec in ledger_read():
        try:
            if not isinstance(rec, dict):
                continue
            if rec.get("kind") != "reply":
                continue
            if rec.get("topicId") != topic_id:
                continue
            if rec.get("status") == "PUBLICATION_FAILED":
                continue
            rt = rec.get("replyTo")
            if rt is None:
                out.append(rec)
                continue
            try:
                k = int(rt)
            except (TypeError, ValueError):
                k = 1  # 判不准 -> 保守按顶层算 (上限 3, 宁可少放行)
            if k == 1:
                out.append(rec)
        except Exception:
            continue
    return out


def account_replies_today(c, uid):
    """这个账号今天一共回了几条(含机器人和号主自己) —— 用 GET /users/{uid}/posts?role=reply。

    平台的回复上限是**号主的**, 机器人的份额必须让开 —— 所以要能看见号主自己的活跃度。
    """
    import datetime as _dt
    try:
        data = c._request("GET", f"/api/v1/users/{uid}/posts?role=reply&limit=50")
    except Exception:
        # 拿不到就返回 None = "不知道号主活跃度" -> 不触发让路, 但其它护栏照常
        return None
    items = (data.get("items") if isinstance(data, dict) else data) or []
    today = _dt.date.today()
    n = 0
    for it in items:
        ca = it.get("createdAt") or ""
        try:
            d = _dt.datetime.fromisoformat(ca.replace("Z", "+00:00")).astimezone().date()
        except Exception:
            continue
        if d == today:
            n += 1
    return n


def owner_active_today(c, uid, bot_count_today=0):
    """号主今天自己发言数(总回复数 - 机器人的)。>= OWNER_ACTIVITY_SOFT_CAP 时机器人让路。"""
    total = account_replies_today(c, uid)
    if total is None:
        return None
    return max(0, total - int(bot_count_today or 0))


def writes_in_last_hour(st):
    """最近一小时 we've written 几条(用于频率闸门)。"""
    import datetime as _dt
    arr = st.get("writes") or []
    now = _dt.datetime.now()
    keep = []
    for t in arr:
        try:
            ts = _dt.datetime.fromisoformat(t)
            if (now - ts).total_seconds() < 3600:
                keep.append(t)
        except Exception:
            continue
    st["writes"] = keep
    return len(keep)


def my_reply_count_in_topic(c, topic_id, uid):
    """我在这个帖里一共回过几条(含顶层与楼中楼) —— 用于"每帖最多回 N 条"护栏。"""
    n = 0
    for it in posts_of(c, topic_id, limit=80):
        if (it.get("author") or {}).get("id") == uid:
            n += 1
    return n


def my_top_level_count(c, topic_id, uid):
    """我在该帖发过的顶层评论数, 以及"这个数是否可信"。

    返回 (count, reliable): reliable=False 表示服务端楼层列表**没取全**
    (翻页中途报错), 此时 count 偏小, 调用方要按"可能更多"保守处理。

    **服务端楼层 与 本地账本取并集**, 不是只看服务端 (2026-11-03 18:xx 修)。

    旧实现只数服务端楼层, 有两个已知的漏算路径, 都会让"每帖顶层评论<=3"
    的护栏**放行更多**:
      1. **索引延迟**: 写操作是异步审核的, 刚过审的评论要过一阵才出现在
         楼层列表里 (memory 里记过: "审核 PUBLISHED 后帖子列表接口有索引
         延迟, 换 sort 参数重读才拿到")。于是"刚发完第 3 条顶层评论, 立刻
         再发第 4 条"时, 服务端只数到 2 条 -> 第 4 条被放行 -> 该帖 4 条顶层评论。
      2. **翻页残缺**: posts_of 遇 PickcatError 会 break, 返回半截列表,
         调用方无从得知, 少算多少全看运气。

    账本 (.wm_ledger.json) 记的是"我发出过的每一次写操作", 是权威来源,
    不受上面两条影响。两边按 postId 去重求并集:
      - 服务端有、账本无 -> 我手动发的 (脚本之外的评论)
      - 账本有、服务端无 -> 刚发出去还没被服务端索引到的
    """
    items, complete = posts_of_full(c, topic_id)
    server_ids = set()
    for it in items:
        if (it.get("author") or {}).get("id") != uid:
            continue
        rtp = it.get("replyToPostNumber")
        if rtp in (None, 1):
            pid = it.get("id")
            server_ids.add(pid if pid else ("pn", it.get("postNumber")))

    n = len(server_ids)
    for rec in ledger_top_level_records(topic_id):
        pid = rec.get("postId")
        key = pid if pid else ("seq", rec.get("seq"))
        if key not in server_ids:
            n += 1
            server_ids.add(key)
    return n, complete


def daily_guard(st, key):
    """返回 (今天计数 dict, 是否超限)。跨天自动清零。

    **计数以账本为准, state 只是缓存。**
    账本 (.wm_ledger.json) 记着每一次写操作的 topicId/replyTo/时间, 是
    "今天到底发了几条顶层评论、几条楼中楼" 的权威依据; 而 .bot_state.json
    是可变的派生缓存, 会因为各种原因丢 (文件损坏、误删、换机器、被
    .gitignore 排除后没带过来) —— 它的 load_state() 还会吞掉 JSON 解析
    异常直接返回 {}。

    老实现只读 state: 状态文件一丢, 当天计数就变成 0/0, 于是
    "每日评论<=3 / 每日回复<=12" 这两条硬上限被静默清零, 机器人当天
    可以再发 3 条顶层评论 + 12 条回复 —— 变成 6 条评论 / 24 条回复,
    超出用户定的规矩一倍。

    现在改成: 从账本重算当天计数, 与 state 取**较大值**。
    取较大值而不是直接覆盖, 是为了同时兜住两种偏差:
      - state 丢了/旧了 -> 账本把真实用量捞回来
      - 账本某条写操作没记上 (极端情况) -> state 不至于把用量记小
    两边一致时就是原值, 行为不变。

    **第三种偏差 (2026-11-03 15:xx 补)**: 账本本身读不出来。
    ledger_read() 在主文件和 .bak 都解析失败时返回 [], 于是账本贡献 0;
    如果 state 同时丢了 (load_state 返回 {}), 两边都是 0/0 ->
    硬上限静默突破。所以这一档要 **fail closed**: 账本不可信且 state
    也覆盖不到今天 -> 按额度已满处理 (宁可今天不写, 也不越限),
    并标出 daily_unknown, 让人知道是"查不到"而不是"真没用过"。
    """
    d = st.setdefault("daily", {})
    if d.get("date") != today_key():
        d["date"] = today_key()
        d["comments"] = 0
        d["replies"] = 0
    d.pop("daily_unknown", None)
    # 用账本校准 (账本是权威)。跨天的记录不参与统计。
    #
    # **计数失败 != 计数为 0** (2026-11-04 01:xx 修):
    # 原来这里 except 掉异常就 led_c, led_r = 0, 0, 于是"算不出来"被当成
    # "今天一条都没发"。只要 state 同时也丢了 (load_state 返回 {}),
    # 两边都是 0/0 -> 硬上限静默突破 (当天实际能再发 3 评论 + 12 回复)。
    # 失效方向仍然是"放行更多" —— 与前几轮修的 state/账本原子写同根因。
    # 现在区分三档: 算出来是多少就是多少 / 算不出来(fail closed) / 账本本身读不出。
    try:
        led_c, led_r = ledger_daily_counts(today_key())
        ledger_count_failed = False
    except Exception:
        led_c, led_r = 0, 0
        ledger_count_failed = True
    d["comments"] = max(int(d.get("comments", 0) or 0), led_c)
    d["replies"] = max(int(d.get("replies", 0) or 0), led_r)

    # 账本在磁盘上却读不出内容 -> 今天的用量**查不到**。
    # 此时 state 若还留着今天的计数就用它 (它是当天的缓存, 通常是对的);
    # state 也丢了/是昨天的 -> 只能 fail closed, 按额度已满处理。
    try:
        from pickcat_client import ledger_unreadable

        unreadable = ledger_unreadable()
    except Exception:
        unreadable = False
    # **算不出来 (计数抛异常) 与 读不出账本 是同一档**: 都意味着
    # "今天的用量查不到"。两者的处理方式必须一致, 否则就是一条绕过
    # fail closed 的路径 (计数异常时走不到那个分支)。
    if unreadable or ledger_count_failed:
        if int(d.get("comments", 0) or 0) <= 0 and int(d.get("replies", 0) or 0) <= 0:
            d["comments"] = MAX_COMMENTS_PER_DAY
            d["replies"] = MAX_REPLIES_PER_DAY
            d["daily_unknown"] = True
    return d


_LEDGER_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _ledger_day_of(rec):
    """从账本记录里取日期 ("YYYY-MM-DD"), 取不出返回 None。"""
    if not isinstance(rec, dict):
        return None
    m = _LEDGER_DATE_RE.match(str(rec.get("at") or "").strip())
    return m.group(0) if m else None


def _ledger_channel(rec):
    """判断一条记录占的是哪个额度: "comment" (顶层评论) 还是 "reply" (楼中楼)。

    优先用显式 channel 字段; 老记录没有则按 replyTo 推断
    (None/1 -> 顶层评论, 其余 -> 楼中楼), 与 create_reply 传参一致。

    **认不准时按顶层评论计** (保守): 顶层上限 3 比回复上限 12 严,
    宁可少放行也不多放行。
    """
    ch = rec.get("channel")
    if ch in ("comment", "reply"):
        return ch
    rt = rec.get("replyTo")
    if rt is None:
        return "comment"
    try:
        n = int(rt)
    except (TypeError, ValueError):
        return "comment"
    return "comment" if n == 1 else "reply"


def _ledger_day_rows(day):
    """返回属于某一天的账本记录 (只含 kind=="reply")。

    **日期读不出来的记录必须被归属到某一天, 不能整条丢掉** (2026-11-04 修):
    原实现 `at[:10] != day -> continue` 会让日期坏掉的记录凭空消失,
    于是今天的真实用量被少算 -> 每日硬上限被静默突破 (失效方向: 放行更多)。

    归属办法 = **相邻记录夹逼**: 账本是按时间追加写的, seq 单调递增,
    所以一条日期坏掉的记录, 它的日期必然落在**前一条可读记录**与
    **后一条可读记录**之间。取这个区间里**最保守**的一端:
      - 区间内包含 day -> 归给 day (它可能就是今天的)
      - 区间内不包含 day -> 归给区间里较晚的那一天 (宁可算到"最近的可能",
        也不让它凭空消失)
      - 前后都没有可读记录 -> 归给 day (整本账本都坏 -> 只能按最坏算)
    这样坏记录不会让任何一天的计数凭空少算, 而真实账本 (日期都好)
    的归属结果与原来完全一致。
    """
    from pickcat_client import ledger_read

    recs = []
    for rec in ledger_read():
        try:
            if isinstance(rec, dict) and rec.get("kind") == "reply":
                recs.append(rec)
        except Exception:
            continue

    days = [_ledger_day_of(r) for r in recs]
    out = []
    for i, rec in enumerate(recs):
        d = days[i]
        if d is not None:
            if d == day:
                out.append(rec)
            continue
        # 日期坏掉了 -> 找前后最近的可读日期来夹逼
        prev_d = None
        for j in range(i - 1, -1, -1):
            if days[j] is not None:
                prev_d = days[j]
                break
        next_d = None
        for j in range(i + 1, len(recs)):
            if days[j] is not None:
                next_d = days[j]
                break
        if prev_d is None and next_d is None:
            out.append(rec)  # 整本账本都坏 -> 按最坏算, 归给今天
            continue
        lo = prev_d if prev_d is not None else next_d
        hi = next_d if next_d is not None else prev_d
        if lo > hi:
            lo, hi = hi, lo
        # 只在 day 落在夹逼区间内时计入 —— 区间外它必然属于别的日子,
        # 算那一天时会被同样地计入, 所以这条记录**恰好被数一次**, 不重复也不丢失。
        if lo <= day <= hi:
            out.append(rec)
    return out


def ledger_daily_counts(day):
    """从本地账本统计某天的 (顶层评论数, 楼中楼回复数)。

    优先用账本里的 channel 字段 ("comment" / "reply") —— 那是写操作时
    显式记下的通道, 最准。老记录没有 channel 字段, 回退到按 replyTo
    推断: replyTo 属于 (None, 1) 的算顶层评论, 其余算楼中楼回复 ——
    与 create_reply 的传参一致 (comment 固定 replyTo=1, reply 传对方楼层号)。
    "at" 字段前 10 位就是日期, 按天切分。

    **单条坏记录不能让整天计数归零** (2026-11-03 15:xx 修):
    账本是手写/程序混合产生的 JSON, 任何一条记录的 replyTo 写成非数字
    (如 "abc") 或 at 格式异常, 都会让 int() 抛 ValueError -> 整个函数
    抛异常 -> daily_guard 吞掉异常 -> 当天计数退回 state 的 0/0 ->
    **硬上限被静默突破**。所以:
      - 每条记录单独 try, 坏记录跳过但**不让整天归零**
      - 坏记录里 replyTo 看着像顶层 (1/None) 的, 仍按顶层评论计
        (保守: 顶层评论上限更严, 宁可少放行也不多放行)

    **日期读不出来的记录也不能直接跳过** (2026-11-04 01:xx 修):
    原实现是 `at[:10] != day 就 continue` —— 日期坏掉的记录被**整条丢掉**,
    于是今天的真实用量被少算, 硬上限照样被突破 (失效方向仍是"放行更多")。
    改用**相邻记录夹逼**归属 (见 _ledger_day_rows)。
    """
    c = 0
    r = 0
    for rec in _ledger_day_rows(day):
        try:
            ch = _ledger_channel(rec)
            if ch == "comment":
                c += 1
            else:
                r += 1
        except Exception:
            continue
    return c, r


def ledger_daily_kind_counts(day):
    """今天楼中楼回复按 (反应式, 主动式, 判不准) 三类统计。

    只数**楼中楼**回复 —— 顶层评论走的是评论额度 (3/天), 不参与回复池的竞争。

    **缺 react 字段的老记录算"判不准"**, 而护栏把"判不准"**保守算主动式**
    (见 ledger_daily_proactive_count): 主动式额度算紧一点, 只是少发几条
    主动内容; 反过来把主动式算成反应式, 就会去挤占给反应式预留的名额 ——
    那正是本轮要修的病根 (11-03: 8 条主动式把额度花光, 5 个真正回我的人
    没回上)。总上限 (12/天) 由 daily_guard 独立兜底, 不受这里的口径影响。
    """
    from pickcat_client import ledger_read

    reactive = proactive = unknown = 0
    for rec in _ledger_day_rows(day):
        try:
            if _ledger_channel(rec) != "reply":
                continue  # 顶层评论占评论额度, 不算
            r = rec.get("react")
            if r is True:
                reactive += 1
            elif r is False:
                proactive += 1
            else:
                unknown += 1
        except Exception:
            continue
    return reactive, proactive, unknown


def ledger_daily_proactive_count(day):
    """今天已用的"主动式"楼中楼回复数 (与反应式抢同一池子的那部分)。

    护栏口径: **主动式 + 判不准** 都算占用主动额度 (保守方向, 见上)。
    """
    _r, proactive, unknown = ledger_daily_kind_counts(day)
    return proactive + unknown


def _reply_is_reactive(c, topic_id, reply_to, uid, notify_id=None):
    """这条楼中楼回复是不是在回应"有人回我"。

    返回 True(反应式) / False(主动式) / None(判不准)。

    判据与 check 的 isReplyToMe **完全一致**: 目标楼层的**父楼层作者是我**
    (rtp 指向首楼时, 首楼作者 = 楼主)。用同一套口径是为了让"记账口径"和
    "需求口径"永远对得上 —— 否则会出现"按 notify 排的需求 vs 按别的口径
    记的账"这种对不上的情况。

    两种信号都算"反应式":
      (a) 带了 --notify —— 那就是 notify 权威列表里的条目 (含 @ 我的提及);
      (b) 父楼层作者是我 (服务端事实, 不依赖调用方是否传 --notify)。

    **判不准时返回 None, 调用方按反应式放行** (宁可少发主动内容, 也不让
    真正回我的人干等)。但账本里会记 react=None, 重建"主动式用量"时
    **保守算主动式** —— 两个方向的保守点不一样:
      - 放行时保守 = 允许发 (别把真·回我的人挡在门外)
      - 记账时保守 = 占用主动额度 (主动内容是可选的, 少发几条无妨)
    """
    if notify_id:
        return True
    try:
        items = posts_of(c, topic_id)
    except Exception:
        return None
    if not items:
        return None
    # 楼层号 -> (作者 id, 父楼层号)
    by_pn = {}
    for it in items:
        try:
            pn = int(it.get("postNumber") or 0)
        except (TypeError, ValueError):
            continue
        by_pn[pn] = ((it.get("author") or {}).get("id"), it.get("replyToPostNumber"))
    # 首楼(#1) 不在 posts 列表里, 用楼主补 —— 与 cmd_check 的 author_by_pn 同源
    try:
        meta = c.get_topic(topic_id)
        by_pn.setdefault(1, ((meta.get("author") or {}).get("id"), None))
    except Exception:
        pass
    try:
        target = by_pn.get(int(reply_to))
    except (TypeError, ValueError):
        return None
    if target is None:
        return None
    rtp = target[1]
    if rtp is None:
        return None
    try:
        parent = by_pn.get(int(rtp))
    except (TypeError, ValueError):
        return None
    if parent is None:
        return None
    if parent[0] is None:
        return None
    return parent[0] == uid


# ---------------------------------------------------------------- check
def _fast_fail_if_down():
    """服务不可用时快速失败: 别再一个个请求去撞墙(每个都要等 10s 超时)。"""
    from pickcat_client import service_is_down

    down, st = service_is_down()
    if down:
        print("=" * 58)
        print("⛔ 服务不可用: 上游 API 连续失败 "
              f"{st.get('consecutive_failures')} 次, 已判定为宕机。")
        print(f"   最后失败: {st.get('last_fail_at')}  (冷却 10 分钟后自动重试)")
        print("   前端壳还在, 但 /api/v1/* 全部 502 —— 这是服务端问题, 不是凭证/配额问题。")
        print("   本轮跳过, 不会发出任何写操作。")
        print("=" * 58)
        return True
    return False


def _is_recent(it, days):
    """楼层是否在最近 N 天内建的 (没有 createdAt 就当不新)。

    用途: 把"回我"的历史楼层限制在合理时间窗内 —— 半年前的回信不该翻出来回。
    """
    ca = it.get("createdAt")
    if not ca:
        return False
    try:
        ts = time.mktime(time.strptime(ca[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    except Exception:
        return False
    return (time.time() - ts) <= days * 86400


def _post_ts(it):
    """楼层的 createdAt -> unix 时间戳 (秒)。取不到就返回 None。"""
    ca = it.get("createdAt")
    if not ca:
        return None
    try:
        return time.mktime(time.strptime(ca[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    except Exception:
        return None


def _burst_siblings(items):
    """把"同一人连着发的几楼"归成组, 返回 {代表楼层号: [同组楼层号...]}。

    背景 (2026-11-03 实测): 用户 c 把一条回答拆成 #17/#18 两楼发 —— 同作者、
    同 replyToPostNumber(=16)、相隔 56 秒。我回了 #18, 内容同时接住了 #17 的观点,
    但"我回过哪些楼层"只按 replyToPostNumber 精确匹配, 于是 #17 仍被当成"没回过",
    下一轮 notify 会把它当新的待回复, 白花回复额度还可能被平台看成刷屏。

    判据 (两条都要满足, 避免把不相干的话题并进来):
      1. 同一作者 且 同一 replyToPostNumber (同一个父楼层)
      2. 相邻两楼的楼层号差 <= BURST_MAX_GAP_FLOORS 且时间差 <= BURST_WINDOW_SEC
    """
    by_parent = {}
    for it in items:
        rtp = it.get("replyToPostNumber")
        a = (it.get("author") or {}).get("id")
        if rtp is None or a is None:
            continue
        by_parent.setdefault((a, int(rtp)), []).append(it)

    groups = {}
    for key, lst in by_parent.items():
        lst.sort(key=lambda x: int(x.get("postNumber") or 0))
        cur = []
        prev = None
        for it in lst:
            pn = int(it.get("postNumber") or 0)
            ts = _post_ts(it)
            if prev is not None:
                ppn = int(prev.get("postNumber") or 0)
                pts = _post_ts(prev)
                gap_ok = (pn - ppn) <= BURST_MAX_GAP_FLOORS
                time_ok = (
                    (ts is None or pts is None)
                    or abs(ts - pts) <= BURST_WINDOW_SEC
                )
                if not (gap_ok and time_ok):
                    # 这一段是独立的连发回答, 收尾并另起一段。
                    if cur:
                        groups[cur[0]] = cur
                    cur = []
            cur.append(pn)
            prev = it
        if cur:
            groups[cur[0]] = cur
    return groups


def answered_floor_numbers(c, topic_id, uid):
    """我已经回过的楼层号集合 (用来判定"这条是不是已经处理过了")。

    三个来源取并集:
      (a) 服务端 —— 我发过的楼层中 replyToPostNumber == N 的那些 N, 说明我在 N 楼下接过话。
          这能覆盖**用户手打**的回复 (那些不进账本)。
      (b) 账本 —— reply 命令会记录 replyTo=N。即使我这层的审核还没过、服务端暂时看不到,
          账本也能证明"我回过了", 避免重复回同一个人。
      (c) **连发合并** —— 对方把一条回答拆成连续几楼时, 我回其中任一楼就算接住了整条。
          见 _burst_siblings。少了这一条, 一条被拆成两楼的回答会被当成两个问题,
          逼我把回复额度浪费在重复作答上 (2026-11-03 实测 c 的 #17/#18 就是这种)。
    """
    out = set()
    items = []
    try:
        items = posts_of(c, topic_id)
        for it in items:
            if (it.get("author") or {}).get("id") != uid:
                continue
            rtp = it.get("replyToPostNumber")
            if rtp is not None:
                out.add(int(rtp))
    except PickcatError:
        pass
    try:
        from pickcat_client import ledger_read

        for r in ledger_read():
            # 每条记录单独 try: 一条坏记录 (replyTo 写成非数字) 不该让
            # **后面所有记录**的 replyTo 都丢失 —— 那会把"我回过的楼层"
            # 判成没回过, 下一轮重复作答白花额度。
            try:
                if r.get("topicId") != topic_id or r.get("kind") != "reply":
                    continue
                rt = r.get("replyTo")
                if rt is not None:
                    out.add(int(rt))
            except (TypeError, ValueError, AttributeError):
                continue
    except Exception:
        pass

    # (c) 连发合并: 我回过的楼层若是某条连发回答的一员, 同组的兄弟楼层也算回过了。
    # 只在"服务端能拉到该帖楼层"时生效 —— 拉不到就退回原来的精确匹配, 不做瞎猜。
    if items:
        try:
            for siblings in _burst_siblings(items).values():
                if any(pn in out for pn in siblings):
                    out.update(siblings)
        except Exception:
            pass
    return out


def cmd_check(c, args):
    if _fast_fail_if_down():
        return
    st = load_state()
    watched = st.setdefault("watched", {})
    uid = client_uid(c)

    # 把自己开的帖子也纳入关注 (别人回我 = 要跟进)
    try:
        for t in my_topics(c, uid):
            tid = t.get("id")
            if tid and tid not in watched:
                watched[tid] = {
                    "title": t.get("title", ""),
                    "last_seen": max_post_number(c, tid),
                    "auto": True,
                }
    except PickcatError as e:
        print(f"# 拉取我的帖子失败: {e}", file=sys.stderr)

    # ① 谁回了我
    pending = []
    anomalies = []
    for tid, info in watched.items():
        try:
            items = posts_of(c, tid)
        except PickcatError as e:
            print(f"# 读取 {tid} 失败: {e}", file=sys.stderr)
            continue
        try:
            meta = c.get_topic(tid)
        except PickcatError as e:
            print(f"# 读取 {tid} 元信息失败: {e}", file=sys.stderr)
            meta = {}
        last = int(info.get("last_seen", 0))
        # 楼层号 -> 作者, 用来判断"这条到底回的是不是我"。
        # 注意: /topics/{id}/posts 不含首楼(#1), 所以 rtp=1 要用"楼主"来当父楼层作者 ——
        # 自己开的帖里 rtp=1 就是回我, 别人开的帖里 rtp=1 只是普通顶层评论。
        topic_author = ((meta.get("author") or {}).get("id"))
        author_by_pn = {1: topic_author}
        for it in items:
            author_by_pn[int(it.get("postNumber") or 0)] = (
                (it.get("author") or {}).get("id")
            )
        # 我已经回过的楼层 —— 用来把"我回过的"和"还没回的"分开。
        answered = answered_floor_numbers(c, tid, uid)
        # 新楼层总数 —— **必须含我自己发的**。
        # 我自己回一句也会把服务端 replyCount 推高, 但我的楼层在下面被 continue 掉了,
        # 早期版本只数"别人发的新楼层", 于是自己回帖后必然误报"列表被截断"。
        new_floors = 0
        for it in items:
            pn = int(it.get("postNumber") or 0)
            if pn <= last:
                continue
            new_floors += 1
        for it in items:
            pn = int(it.get("postNumber") or 0)
            a = it.get("author") or {}
            if a.get("id") == uid:
                continue  # 自己发的跳过
            rtp = it.get("replyToPostNumber")
            parent_id = author_by_pn.get(int(rtp)) if rtp is not None else None
            is_mine = parent_id == uid
            if is_mine and pn in answered:
                continue  # 我已经回过这一层了
            # "回我"的楼层判定**不看 watermark**, 只看"是否已回过" + "是否够新"。
            # 原因: 回帖动作会把 last_seen 推到该帖最大楼层号, 于是"我回了高层楼、
            # 低层还没回"的情况会被 watermark 永久吞掉 —— 那种回信就再也捡不回来了。
            # 但历史楼层(几个月前的)不该翻出来回, 所以用 createdAt 卡一个时间窗。
            if is_mine:
                if pn <= last and not _is_recent(it, REPLY_HORIZON_DAYS):
                    continue
            elif pn <= last:
                continue  # 跟我无关的老楼层, 降噪
            pending.append(
                {
                    "topicId": tid,
                    # 标题兜底: watched 里的 title 是当初 watch 时记的, 可能为空
                    # (比如是我自己发的帖, 或 notify 顺手加进关注的)。
                    # 标题为空时用服务端元信息补上 —— 否则输出里"帖子:"后面是空的,
                    # 根本无法判断是哪条待回复, 只能靠 topicId 反查。
                    "title": info.get("title") or meta.get("title") or "",
                    "postNumber": pn,
                    "replyToPostNumber": rtp,
                    "parentAuthor": parent_id,
                    # 真正的"回我": 父楼层是我 —— 楼中楼回我, 或者 (rtp=1/None) 回我的帖首楼
                    "isReplyToMe": is_mine,
                    "answered": pn in answered,
                    "author": a.get("username"),
                    "authorId": a.get("id"),
                    "text": strip_html(it.get("cookedHtml") or it.get("markdown"))[:300],
                }
            )
        # 注意: 这里不推进 last_seen —— 只有真正回过 (reply/comment) 才记账,
        # 这样本轮没处理掉的回复, 下一轮还会被拾起来, 不会被永久跳过。

        # 截断感知: 服务端可能只返回部分回复 (repliesTruncated)。
        # 此时"看不到"不等于"没人回", 必须显式报出来, 不能让 Agent 误判。
        rc = meta.get("replyCount")
        trunc = meta.get("repliesTruncated")
        # 注意: repliesTruncated 在这 API 上基本常驻 true, 不能单独当告警依据。
        #
        # **唯一可靠的截断信号: replyCount 比我们实际取到的楼层数还大。**
        # posts_of 已经翻页取全, 实测 32 个关注帖里 31 个满足 rc == len(items)
        # (唯一例外是一个有 19 个楼层但 rc=19 而 items=20 的帖, 是被删楼导致的, 不是截断)。
        # 所以 rc > len(items) 才是真截断。
        #
        # 早期版本用的是"replyCount 增量 vs 新楼层数", 这个判据是错的:
        # last_seen 在我每次回帖后被推到该帖最大楼层号, 于是"别人新发的楼"
        # 会被算进 last_seen 之内而不计入 new_floors —— 只要该帖有人在我回帖后
        # 又发了楼, 就会误报"列表被截断"(11-02 实测: 鲸鱼娘帖 rc 6->9 但只见 1 层新楼)。
        # 结论: 增量判据永远误报, 只有绝对值判据 (rc > len(items)) 可靠。
        if rc is not None and rc > len(items):
            anomalies.append(
                f"《{info.get('title','')[:24]}》replyCount={rc} > 实际取到 {len(items)} 条 "
                f"(列表被截断) —— 别当成'没人回'"
            )
        if rc is not None:
            info["reply_count"] = rc
        la = meta.get("lastActivityAt")
        prev_la = info.get("last_activity_at")
        # lastActivityAt 变化本身不说明有隐藏楼层 (改标题/编辑也算活动),
        # 只有配合上面的 rc > len(items) 才有意义, 这里不再单独告警。
        if la:
            info["last_activity_at"] = la
    # ② AI 帖候选 (排除已关注 / 已评论过)
    candidates = []
    try:
        feed = c._request("GET", f"/api/v1/topics?limit={FEED_SCAN}")
        items = (feed.get("items") if isinstance(feed, dict) else feed) or []
        for it in items:
            tid = it.get("id")
            if not tid or tid in watched:
                continue
            title = it.get("title", "")
            excerpt = strip_html(it.get("excerpt") or "")[:140]
            if not AI_RE.search(title + " " + excerpt):
                continue
            tl, _tl_ok = my_top_level_count(c, tid, uid)
            candidates.append(
                {
                    "topicId": tid,
                    "title": title,
                    "author": (it.get("author") or {}).get("username"),
                    "excerpt": excerpt,
                    "myTopLevel": tl,
                }
            )
            if len(candidates) >= MAX_CANDIDATES:
                break
    except PickcatError as e:
        print(f"# feed 拉取失败: {e}", file=sys.stderr)

    save_state(st)  # 只更新 last_seen 记账, 不动内容

    if args.json:
        print(json.dumps({"pendingReplies": pending, "candidates": candidates,
                          "anomalies": anomalies},
                         ensure_ascii=False, indent=2))
        return

    print("=" * 60)
    try:
        from pickcat_client import quota_read, quota_remaining

        q = quota_read()
        left = quota_remaining()
        if q and left > 0:
            print(f"[⛔ 配额限制中] {q.get('action')} {q.get('used')}/{q.get('limit')} "
                  f"已用尽, 距恢复 {left}s ({q.get('availableAt')}) —— 本轮不要尝试发帖/评论")
        elif q:
            print(f"[配额] {q.get('action')} 上次用量 {q.get('used')}/{q.get('limit')}, 现已恢复")
    except Exception:
        pass
    # 脚本内每日额度 —— 与平台配额是两回事, 两者都要看。
    # 满了就是本轮零写入, 不是故障, 不要去撞护栏。
    # **必须走 daily_guard**: 它负责跨天清零。直接读 st["daily"] 会把
    # 昨天的数字(比如 10/12)原样显示出来, 让后续轮次误以为额度快满了。
    _d = daily_guard(st, "replies")
    save_state(st)  # 把跨天清零的结果落盘, 否则下次读到的还是昨天的计数
    # 主动式(我自选话题)与反应式(有人回我)分开显示 —— 主动式有单独上限,
    # 剩下的额度预留给真正回我的人 (2026-11-03 22:xx 加)。
    _react_n, _pro_n, _unk_n = ledger_daily_kind_counts(today_key())
    _pro_used = _pro_n + _unk_n
    print(f"[每日额度] 评论 {_d.get('comments', 0)}/{MAX_COMMENTS_PER_DAY}, "
          f"回复 {_d.get('replies', 0)}/{MAX_REPLIES_PER_DAY} "
          f"(其中主动式 {_pro_used}/{MAX_PROACTIVE_REPLIES_PER_DAY}"
          + (f", 含判不准 {_unk_n} 条" if _unk_n else "")
          + f", 预留给'有人回我' {_d.get('replies', 0) - _pro_used}/"
          f"{MAX_REPLIES_PER_DAY - MAX_PROACTIVE_REPLIES_PER_DAY}) "
          f"({_d.get('date', '-')}, 按自然日清零)")
    if _d.get("daily_unknown"):
        print("[⚠ 额度未知] 账本读不出内容且 state 也没有今天的计数 —— "
              "已按额度已满处理 (宁可今天不写也不越限)。请检查 .wm_ledger.json。")
    if anomalies:
        print(f"[⚠ 数据异常] {len(anomalies)} 条 —— 列表可能被服务端截断, 别当成'没人回'")
        for a in anomalies:
            print("  - " + a)
        print("-" * 60)
    # 按"是否真的回我"排序: 真·回我的排前面, 同一档内按楼层号小(先到先回)。
    # 顶层评论(rtp=1)也算回我 —— 那是我发帖后别人接的第一层。
    pending.sort(key=lambda p: (not p["isReplyToMe"], p["postNumber"]))

    real = [p for p in pending if p["isReplyToMe"]]
    print(f"[待回复] {len(pending)} 条 (其中真·回我 {len(real)} 条)")
    for p in pending:
        tag = "回我" if p["isReplyToMe"] else "非回我"
        print(f"  帖子: {p['title']}")
        print(f"    topicId={p['topicId']}  楼层 #{p['postNumber']} "
              f"(replyTo={p['replyToPostNumber']})  来自 {p['author']}  [{tag}]")
        print(f"    内容: {p['text']}")
    print("-" * 60)
    print(f"[AI 帖候选] {len(candidates)} 个 (我已评顶层评论数已在括号里, >=3 会被护栏拦)")
    for cd in candidates:
        print(f"  - {cd['title']}")
        print(f"    topicId={cd['topicId']}  by {cd['author']}  (我的顶层评论 {cd['myTopLevel']})")
        print(f"    摘要: {cd['excerpt']}")
    print("=" * 60)


# ---------------------------------------------------------------- reply
def cmd_reply(c, args):
    # **整段进跨进程排他锁** (2026-11-03 20:xx 修):
    # 硬上限的判定是"读计数 -> 比较 -> 发请求", 不加锁就是 check-then-act 竞态 ——
    # 并发实例各自读到同一个旧计数, 都会通过护栏, 于是实际发出条数超限。
    # (实测: 预置 11 条 + 4 个并发实例 -> 4 条全发出, 突破 12 的上限。)
    # 锁的粒度是"整个判定+写入", 后一个实例拿到锁时能看到前一个刚写进账本的记录。
    with write_lock():
        return _cmd_reply_locked(c, args)


def _cmd_reply_locked(c, args):
    st = load_state()
    uid = client_uid(c)
    d = daily_guard(st, "replies")

    # **顶层评论必须走"评论"预算, 不能走"回复"预算。**
    # 顶层评论 = replyToPostNumber 指向首楼(1)或为空, 和 cmd_comment 发出去的
    # 是同一种东西 (服务端帖子结构完全一样)。早期版本这里一律按 reply 计,
    # 于是 `reply --reply-to 1` 成了绕过"每日评论上限 3"的后门:
    # 一天的顶层评论能发到 12 条 (回复预算) 而不是 3 条。
    # 账本 channel 也要跟着记 comment, 否则重建计数时口径又对不上了。
    is_top = args.reply_to in (0, 1, None)

    # 护栏 1: 顶层评论每帖 <= 3
    if is_top:
        cnt, tl_ok = my_top_level_count(c, args.topic_id, uid)
        if not tl_ok:
            # 楼层列表没取全 -> 计数偏小, 可能其实已经 >=3 了。
            # 查不到就当"已满": 这个护栏失效的方向是"放行更多" (超发),
            # 而用户定的每帖顶层上限是硬约束, 宁可这次不评。
            print(f"[护栏] 该帖楼层列表没取全 (翻页中途失败), 无法确认我的顶层评论数, "
                  f"按已满处理 (上限 {MAX_TOP_LEVEL})。楼中楼回复不受限。", file=sys.stderr)
            sys.exit(3)
        if cnt >= MAX_TOP_LEVEL:
            print(f"[护栏] 该帖我的顶层评论已 {cnt} 条 (上限 {MAX_TOP_LEVEL}), "
                  f"拒绝再发顶层评论。楼中楼回复不受限。", file=sys.stderr)
            sys.exit(3)
        # 护栏 2: 每日**评论**上限 (顶层评论占的是评论额度)
        if d["comments"] >= MAX_COMMENTS_PER_DAY:
            print(f"[护栏] 今日评论已达上限 {MAX_COMMENTS_PER_DAY}, 跳过。",
                  file=sys.stderr)
            sys.exit(3)
    # 护栏 0: 号主今天自己说得多了 -> 机器人本轮让路(把额度全留给号主)
    owner_n = owner_active_today(c, uid, d.get("replies", 0))
    if owner_n is not None and owner_n >= OWNER_ACTIVITY_SOFT_CAP:
        print(f"[让路] 号主今天已自己发言 {owner_n} 条 (>= {OWNER_ACTIVITY_SOFT_CAP}), "
              f"机器人本轮闭嘴。", file=sys.stderr)
        sys.exit(3)
    # 护栏 0.5: 频率闸门 —— 每小时最多写 MAX_WRITES_PER_HOUR 条
    if writes_in_last_hour(st) >= MAX_WRITES_PER_HOUR:
        print(f"[护栏] 最近一小时已写 {MAX_WRITES_PER_HOUR} 条, 放慢。", file=sys.stderr)
        sys.exit(3)

    # 护栏 1.5: 同一帖最多回 N 条(防刷屏; 社区明确提过"楼中楼也适当少点")
    if args.reply_to not in (0, 1, None):
        per_topic = my_reply_count_in_topic(c, args.topic_id, uid)
        if per_topic >= MAX_REPLIES_PER_TOPIC:
            print(f"[护栏] 该帖我已经回过 {per_topic} 条 (上限 {MAX_REPLIES_PER_TOPIC}), 不刷屏。",
                  file=sys.stderr)
            sys.exit(3)

    # 护栏 3: 每日回复上限 (楼中楼回复才占回复额度)
    # 注: 这里原本是 `if is_top:` 的 else 分支, 中间被插入了独立的护栏块(让路/频率/每帖上限),
    # 配对被拆散 -> else 改绑到上面的 `if args.reply_to not in (...)` 上, 导致楼中楼回复
    # 跳过"每日回复上限"且 reactive 未赋值(UnboundLocalError)。这里等价改写为 `if not is_top:`。
    if not is_top:
        if d["replies"] >= MAX_REPLIES_PER_DAY:
            print(f"[护栏] 今日回复已达上限 {MAX_REPLIES_PER_DAY}, 跳过。", file=sys.stderr)
            sys.exit(3)
        # 护栏 3b: **主动式 (我自选话题去答) 单独限量**, 把额度预留给
        # "有人回我"的人。
        #
        # 为什么要有这条 (2026-11-03 22:xx 修): 反应式和主动式抢同一个池子,
        # 而需求是**双峰**的 —— 实测"有人回我"的通知集中在 10-12 点和 20-21 点,
        # 凌晨 00-09 点几乎没有。11-03 我在 00:53-08:48 把 12 条额度里的
        # 8 条花在主动式内容上, 结果 11:42 之后陆续来的 5 个真·回我的人
        # 一条都没回上 (之后连续 8 轮零输出)。额度不是不够, 是用在了错的时间、
        # 错的对象上。
        #
        # 判定用**客观口径** (目标楼层的父楼层作者是我), 与 check 的 isReplyToMe
        # 同一套判据 —— 记账口径和需求口径必须对得上。
        # 判不准时(None)按反应式放行: 宁可少发主动内容, 也不让人干等。
        # 防御: 无论走哪个分支, reactive 都有值(None = 判不准, 按反应式放行)
        reactive = None
        reactive = _reply_is_reactive(
            c, args.topic_id, args.reply_to, uid, getattr(args, "notify", None)
        )
        if reactive is False:
            pro = ledger_daily_proactive_count(today_key())
            if pro >= MAX_PROACTIVE_REPLIES_PER_DAY:
                print(f"[护栏] 今日主动回复 (我自选话题) 已达上限 "
                      f"{MAX_PROACTIVE_REPLIES_PER_DAY}, 跳过 —— 剩下的额度"
                      f"预留给'有人回我'的人。", file=sys.stderr)
                sys.exit(3)

    # reply_to=0 不是合法楼层号, 按顶层评论处理 (与 cmd_comment 走同一条路径)
    reply_to = 1 if is_top else args.reply_to
    resp = c.create_reply(
        args.topic_id, args.body, reply_to_post_number=reply_to,
        channel="comment" if is_top else "reply",
        react=(True if is_top else (None if reactive is None else reactive)),
    )
    if is_top:
        d["comments"] += 1
    else:
        d["replies"] += 1
    # 顺手关注这个帖 (以后有人回我继续跟)
    w = st.setdefault("watched", {})
    if args.topic_id not in w:
        w[args.topic_id] = {"title": args.title or "", "last_seen": 0}
    w[args.topic_id]["last_seen"] = _advance_last_seen(c, args.topic_id, w)
    # 标记通知已处理。注意必须写进**同一个** st: 之前另开 st2 存一次,
    # 会被下面这行 save_state(st) 覆盖掉, 导致下一轮重复回同一条。
    if getattr(args, "notify", None):
        handled = st.setdefault("notified_handled", [])
        if args.notify not in handled:
            handled.append(args.notify)
    save_state(st)
    if getattr(args, "notify", None):
        print(f"[ok] 通知已标记处理: {args.notify}", file=sys.stderr)
    print(json.dumps(resp, ensure_ascii=False))
    import datetime as _dt
    st.setdefault("writes", []).append(_dt.datetime.now().isoformat(timespec="seconds"))
    st["writes"] = st["writes"][-50:]
    save_state(st)
    print("[ok] 已提交, 状态: " + str(resp.get("status")), file=sys.stderr)


# ---------------------------------------------------------------- comment
def cmd_comment(c, args):
    """在已有帖子下评一条顶层评论 (受每帖<=3 与每日上限约束)。

    整段进跨进程排他锁 —— 理由同 cmd_reply: "读计数 -> 判定 -> 发"必须原子,
    否则并发实例会各自通过护栏, 实际发出条数超限。
    """
    with write_lock():
        return _cmd_comment_locked(c, args)


def _cmd_comment_locked(c, args):
    st = load_state()
    uid = client_uid(c)
    d = daily_guard(st, "comments")

    cnt, tl_ok = my_top_level_count(c, args.topic_id, uid)
    if not tl_ok:
        # 楼层列表没取全 -> 计数偏小, 可能其实已经 >=3 了。查不到就当"已满"。
        print(f"[护栏] 该帖楼层列表没取全 (翻页中途失败), 无法确认我的顶层评论数, "
              f"按已满处理 (上限 {MAX_TOP_LEVEL})。", file=sys.stderr)
        sys.exit(3)
    if cnt >= MAX_TOP_LEVEL:
        print(f"[护栏] 该帖我的顶层评论已 {cnt} 条 (上限 {MAX_TOP_LEVEL}), 拒绝。",
              file=sys.stderr)
        sys.exit(3)
    if d["comments"] >= MAX_COMMENTS_PER_DAY:
        print(f"[护栏] 今日评论已达上限 {MAX_COMMENTS_PER_DAY}, 跳过。", file=sys.stderr)
        sys.exit(3)

    resp = c.create_reply(args.topic_id, args.body, reply_to_post_number=1,
                           channel="comment")
    d["comments"] += 1
    w = st.setdefault("watched", {})
    if args.topic_id not in w:
        w[args.topic_id] = {"title": args.title or "", "last_seen": 0}
    w[args.topic_id]["last_seen"] = _advance_last_seen(c, args.topic_id, w)
    save_state(st)
    print(json.dumps(resp, ensure_ascii=False))
    print("[ok] 已提交, 状态: " + str(resp.get("status")), file=sys.stderr)


# ---------------------------------------------------------------- watch
def cmd_watch(c, args):
    st = load_state()
    w = st.setdefault("watched", {})
    tid = args.topic_id
    ls = args.last_seen if args.last_seen is not None else max_post_number(c, tid)
    w[tid] = {"title": args.title or w.get(tid, {}).get("title", ""), "last_seen": ls}
    save_state(st)
    print(f"[ok] 已关注 {tid}, last_seen={ls}")


def cmd_refresh(c, args):
    """复查未完成的投稿: 更新审核状态, 并把配额信息落盘。

    配额是**异步失败**的 —— POST 只返回 202, 要查审核状态才看得到 QUOTA_EXCEEDED。
    所以必须主动复查, 否则会一次次白跑。
    """
    from pickcat_client import ledger_read, quota_save, quota_from_submission, quota_remaining

    if _fast_fail_if_down():
        return
    # 先按快照查出要复查的投稿 (网络调用在锁外, 不占着锁干等)。
    snapshot = ledger_read()
    changed = []
    quota = None
    # 收集**状态增量**, 而不是就地改快照 —— 见下面写回处的说明。
    updates = {}
    for r in snapshot:
        sid = r.get("submissionId")
        if not sid or r.get("status") not in ("PENDING_PROVIDER", "PENDING_PUBLICATION"):
            continue
        try:
            rec = c.get_post_submission(sid)
        except PickcatError as e:
            print(f"  查询失败 {sid}: {e}", file=sys.stderr)
            continue
        st = rec.get("status")
        if st != r.get("status"):
            changed.append((r.get("seq"), r.get("status"), st))
        upd = {"status": st}
        if st == "PUBLICATION_FAILED":
            upd["reasonCode"] = rec.get("reasonCode")
            q = quota_from_submission(rec)
            if q:
                quota = q
                quota_save(q)
            upd["reasonDetails"] = rec.get("reasonDetails")
        updates[sid] = upd
    # 回写账本 (原子写)。
    # ⚠ 这里的写回是"每轮都执行"的, 所以它同时是危险的: 万一读到的账本
    #   是空的 (主文件损坏 + 备份也坏了), 这行就把空列表写回去, 把历史
    #   **永久**抹掉 —— 每日计数随之归零, 硬上限静默失效。
    #   所以: 账本本来有内容、这轮却读到空 -> 拒绝写回, 保留现场。
    # **主备两份都要看** (2026-11-03 16:xx 修): 只看主文件的话,
    #   "主文件丢失 + 备份也坏了" 时 prev_size=0, 闸门不触发,
    #   空列表会把仅存的备份现场一起抹掉。
    #
    # **写回必须"重读 + 只应用增量", 不能把旧快照整个写回去**
    # (2026-11-03 20:xx 修): 复查要发网络请求, 这期间另一个实例可能
    # 刚往账本里追加了新记录。若把开始时的旧快照整个写回去, 那条新记录
    # 就被**抹掉**了 —— 账本少一条 = 当天计数少算 1 = 硬上限的计数依据失真
    # (失效方向: 放行更多)。这跟"账本损坏被写空"是同一类事故。
    # 所以这里在锁内**重新读一遍**账本, 只把上面查到的状态增量贴上去。
    from pickcat_client import _ledger_paths

    ledger_path, ledger_bak_path = _ledger_paths()
    try:
        prev_size = max(
            ledger_path.stat().st_size if ledger_path.exists() else 0,
            ledger_bak_path.stat().st_size if ledger_bak_path.exists() else 0,
        )
    except Exception:
        prev_size = 0
    try:
        from pickcat_client import ledger_write_all

        with write_lock():
            ledger = ledger_read()          # 重读: 拿到包含并发新记录的最新版
            if prev_size > 2 and not ledger:
                print(f"  ⚠ 账本读回为空但文件非空 ({prev_size} 字节) —— 拒绝写回, "
                      f"保留原文件现场 (可能是账本损坏)", file=sys.stderr)
                print("  复查了 0 条账本记录 (账本读回异常, 已跳过写回)")
                print("=" * 62)
                return
            # 只贴状态增量, 不动其他字段 (并发追加的记录原样保留)。
            for r in ledger:
                upd = updates.get(r.get("submissionId"))
                if not upd:
                    continue
                r["status"] = upd["status"]
                if "reasonCode" in upd:
                    r["reasonCode"] = upd["reasonCode"]
                if "reasonDetails" in upd:
                    r["reasonDetails"] = upd["reasonDetails"]
            ledger_write_all(ledger)
    except Exception as e:
        print(f"账本写回失败: {e}", file=sys.stderr)

    print(f"复查了 {len(ledger)} 条账本记录")
    if changed:
        for seq, old, new in changed:
            print(f"  seq={seq}: {old} -> {new}")
    else:
        print("  状态无变化")
    if quota:
        print(f"  ⚠ 配额用尽: {quota['action']} {quota['used']}/{quota['limit']} "
              f"(窗口 {quota.get('windowSeconds')}s), 恢复时间 {quota['availableAt']}")
        print(f"    距恢复还有 {quota_remaining()} 秒 —— 期间所有写操作会被闸门拦住")
    else:
        left = quota_remaining()
        print(f"  当前无配额限制" + (f" (距恢复 {left}s)" if left else ""))


def cmd_quota(c, args):
    from pickcat_client import quota_read, quota_remaining

    q = quota_read()
    if not q:
        print("未记录到配额限制 (还没撞过额度)")
        return
    left = quota_remaining()
    print("=== 配额状态 ===")
    print(f"  动作    : {q.get('action')}")
    print(f"  用量    : {q.get('used')}/{q.get('limit')}")
    print(f"  窗口    : {q.get('windowSeconds')} 秒")
    print(f"  恢复时间: {q.get('availableAt')}")
    print(f"  剩余    : {left} 秒" + ("  (仍在限制中, 写操作会被拦)" if left else "  (已恢复)"))
    print(f"  检测于  : {q.get('detectedAt')}")


def _fetch_notifications(c, limit=50, max_pages=8):
    """翻页拉全量通知。

    服务端 /api/v1/notifications 的 limit 上限是 50 (给 100 直接 400),
    且支持 pageInfo.nextCursor 续取更早的页。
    **只拉一页 (哪怕 limit=50) 依然是盲区** —— 社区一天能攒几十条通知,
    一页盖不住, 更早的未处理通知会被整段漏掉。
    11-03 实测: 单页 limit=50 时 notify 报"0 条待回复", 翻页到第 3 页
    才捞出两条真·回我从未被答复的楼层 (c#15 / Helen.S#28)。
    """
    items = []
    cursor = None
    for _ in range(max_pages):
        path = f"/api/v1/notifications?limit={limit}"
        if cursor:
            path += f"&cursor={cursor}"
        data = c._request("GET", path)
        page = (data.get("items") if isinstance(data, dict) else data) or []
        items.extend(page)
        pi = (data.get("pageInfo") or {}) if isinstance(data, dict) else {}
        if not pi.get("hasNextPage"):
            break
        cursor = pi.get("nextCursor")
        if not cursor:
            break
    return items


def _mention_targets_me(details, my_username):
    """这条 USER_MENTIONED 是不是真的 @ 了我 (而不是 @ 全站)。

    社区里有一种常见玩法: 发个"挑战艾特所有人"的帖, 正文挂一长串 @,
    谁被 @ 到都会收到一条 USER_MENTIONED。那种**不该算"有人找我"** ——
    逐个去回既刷屏又没意义 (2026-10-18 实测: starry 的挑战帖 excerpt 里
    是一长串用户名, 我自己那截断的片段里根本没出现我的名字)。

    判据 = 提及正文里能找到 "@<我的用户名>"。找不到就说明这条不是专门叫我。
    excerpt 服务端会截断, 所以"没找到"只能判为不是, 不能反推成是 —— 宁可漏,
    不误报 (误报会白烧回复额度)。
    """
    if not my_username:
        return False
    ex = str((details or {}).get("excerpt") or "")
    if not ex:
        return False
    me = str(my_username).strip()
    if not me:
        return False
    low = ex.lower()
    for cand in {me, me.lower(), me.upper()}:
        if ("@" + cand) in low:
            return True
    return False


def cmd_notify(c, args):
    """列出"谁回了我"—— 直接读通知, 不用再靠扫帖子猜。

    /api/v1/notifications 的 REPLY_CREATED 类型自带:
      actor(谁) / topic(哪个帖) / post.postNumber(几楼)
    比"扫帖子 + 猜谁回我"可靠得多: 不会误报, 也不会被回复列表截断坑到。

    处理完一条通知后, 用 `reply --notify <通知id>` 顺手标记为已处理。
    """
    st = load_state()
    seen = st.setdefault("notified_handled", [])
    items = _fetch_notifications(c)
    pending = []
    mentions = []
    stale = []
    kinds = {}
    ans_cache = {}
    uid = client_uid(c)
    try:
        my_username = (c.get_profile() or {}).get("username")
    except Exception:
        my_username = None
    for it in items:
        t = it.get("type")
        kinds[t] = kinds.get(t, 0) + 1
        # USER_MENTIONED (别人 @ 我) 也是一种"该回应我"的形态, 早期版本只认
        # REPLY_CREATED, 于是 @ 我提问的帖子会被整段漏掉 (11-03 发现)。
        # 但 @ 全站的挑战帖会误报, 所以单独归类, 用 _mention_targets_me 二次确认。
        if t not in ("REPLY_CREATED", "USER_MENTIONED") or it.get("id") in seen:
            continue
        d = it.get("details") or {}
        if t == "USER_MENTIONED" and not _mention_targets_me(d, my_username):
            continue
        actor = d.get("actor") or {}
        topic = d.get("topic") or {}
        post = d.get("post") or {}
        row = {
            "notificationId": it.get("id"),
            "actor": actor.get("username"),
            "actorId": actor.get("id"),
            "topicId": topic.get("id"),
            "topicTitle": topic.get("title"),
            "postNumber": post.get("postNumber"),
            "postId": post.get("id"),
            "at": it.get("createdAt"),
            "kind": t,
        }
        # **通知 ≠ 还没回**: 通知只说明"有人回过我", 不管我后来回没回。
        # 早期版本直接把所有未见过的 REPLY_CREATED 当待回复, 于是那些我在
        # 上一轮已经回过的楼层, 会因为漏传 --notify 而每轮重复冒出来
        # (11-02 实测: 4 条待回复里 3 条是 11-01 就已回过的陈旧通知)。
        # 判据 = 那个楼层我是否已经接过话 (服务端楼层 + 本地账本, 见 answered_floor_numbers)。
        #
        # 时间窗: 跟 check 用同一个 REPLY_HORIZON_DAYS。翻页后会把 10 月的
        # 旧通知一起捞上来, 那些不该再回 (半年前的回信翻出来回很怪),
        # 只作标记处理, 不进待回复。
        if not _is_recent(it, REPLY_HORIZON_DAYS):
            row["answered"] = True
            row["reason"] = "超出时间窗"
            stale.append(row)
            continue
        # 同一帖的多条通知共用一次楼层查询 —— answered_floor_numbers 会拉
        # 整个帖子, 重复调用纯属浪费 API (每条通知一次往返)。
        if row["topicId"] not in ans_cache:
            try:
                ans_cache[row["topicId"]] = answered_floor_numbers(c, row["topicId"], uid)
            except Exception:
                ans_cache[row["topicId"]] = set()
        answered = ans_cache[row["topicId"]]
        if row["postNumber"] is not None and int(row["postNumber"]) in answered:
            row["answered"] = True
            stale.append(row)
        elif t == "USER_MENTIONED":
            # 单独归类: @ 我 不等于"回我", 但同样值得回应。
            row["answered"] = False
            mentions.append(row)
        else:
            row["answered"] = False
            pending.append(row)
    # 陈旧通知顺手标记掉, 免得每轮都重复冒出来白耗回复额度。
    if stale:
        for row in stale:
            if row["notificationId"] not in seen:
                seen.append(row["notificationId"])
        save_state(st)
    print("=" * 62)
    print(f"[通知] 最近 {len(items)} 条: {kinds}")
    if pending:
        print(f"[待回复] {len(pending)} 条 (确认回过我的, 且我还没回过):")
        for p in pending:
            print(f"  {p['actor']} 在《{p['topicTitle']}》#{p['postNumber']} 回了我")
            print(f"    topicId={p['topicId']}  notificationId={p['notificationId']}")
    else:
        print("[待回复] 0 条 (没有未处理的回复通知)")
    if mentions:
        print(f"[提及] {len(mentions)} 条 (有人 @ 我, 值得回应):")
        for p in mentions:
            print(f"  {p['actor']} 在《{p['topicTitle']}》#{p['postNumber']} @ 了我")
            print(f"    topicId={p['topicId']}  notificationId={p['notificationId']}")
    if stale:
        print(f"[已回过] {len(stale)} 条 (陈旧通知: 我早已接过话, 已自动标记处理)")
        for p in stale:
            print(f"  {p['actor']} 《{p['topicTitle']}》#{p['postNumber']}")
    print("=" * 62)


def cmd_limits(c, args):
    """把整套限制摊开: 平台硬上限 / 机器人份额 / 号主余量 / 今日实际用量。"""
    st = load_state()
    d = daily_guard(st, "replies")
    save_state(st)
    uid = client_uid(c)
    total_today = account_replies_today(c, uid)
    bot_today = int(d.get("replies", 0))
    owner_today = (max(0, total_today - bot_today) if total_today is not None else None)
    print("=" * 64)
    print("【限制总表】平台硬上限是号主的, 机器人只占一小份")
    print(f"  {'':<20}{'平台上限':>10}{'机器人份额':>12}{'留给号主':>10}")
    print(f"  {'回复(含楼中楼)':<18}{PLATFORM_REPLY_LIMIT_PER_DAY:>12}"
          f"{MAX_REPLIES_PER_DAY:>12}{max(0, PLATFORM_REPLY_LIMIT_PER_DAY - MAX_REPLIES_PER_DAY):>10}")
    print(f"  {'发主题帖':<19}{PLATFORM_TOPIC_LIMIT_PER_DAY:>12}"
          f"{min(MAX_TOPICS_PER_DAY, MAX_PROACTIVE_PER_DAY):>12}"
          f"{max(0, PLATFORM_TOPIC_LIMIT_PER_DAY - min(MAX_TOPICS_PER_DAY, MAX_PROACTIVE_PER_DAY)):>10}")
    print(f"  {'评论(去陌生帖插嘴)':<17}{'(同回复池)':>12}{MAX_COMMENTS_PER_DAY:>12}{'-':>10}")
    print(f"  {'主动池(发帖+盖楼)':<16}{'-':>12}{MAX_PROACTIVE_PER_DAY:>12}{'-':>10}")
    print(f"  {'每帖顶层评论':<18}{'-':>12}{MAX_TOP_LEVEL:>12}{'-':>10}")
    print(f"  {'每帖回复(含楼中楼)':<17}{'-':>12}{MAX_REPLIES_PER_TOPIC:>12}{'-':>10}")
    print(f"  {'每小时写操作':<19}{'-':>12}{MAX_WRITES_PER_HOUR:>12}{'-':>10}")
    print(f"  {'两次写最小间隔':<18}{'-':>12}{int(WRITE_INTERVAL)}s{'-':>10}")
    print(f"  {'号主活跃让路阈值':<17}{'-':>12}{OWNER_ACTIVITY_SOFT_CAP:>12}{'-':>10}")
    print("-" * 64)
    print(f"  今日机器人: 回复 {bot_today}/{MAX_REPLIES_PER_DAY}, 评论 {d.get('comments', 0)}/{MAX_COMMENTS_PER_DAY}")
    print(f"  今日账号共回 {total_today if total_today is not None else '?'} 条, "
          f"其中号主自己 {owner_today if owner_today is not None else '?'} 条")
    print(f"  号主活跃 >= {OWNER_ACTIVITY_SOFT_CAP} 时机器人自动让路闭嘴")
    print("=" * 64)


def cmd_quip_scan(c, args):
    """给"选材"用的预览: AI 相关帖子 + 它们的正文 + 本人语录库。

    分工: 工具**取材 + 执行 + 护栏**, 由 LLM(或本人)决定**哪一句配得上哪个帖**。
    关键词打分只作提示, 语义贴合度最终靠人/模型判断 —— 宁可少发, 不要硬凑。
    """
    from pickcat_quotes import build_corpus, load_corpus, match_quotes

    st = load_state()
    uid = client_uid(c)
    corpus = load_corpus(QUOTE_UID)
    if not corpus or args.rebuild:
        corpus = build_corpus(c, QUOTE_UID)
        from pickcat_quotes import save_corpus
        save_corpus(QUOTE_UID, corpus)
    feed = c._request("GET", f"/api/v1/topics?limit={args.limit}")
    items = (feed.get("items") if isinstance(feed, dict) else feed) or []
    print("=" * 70)
    print(f"【选材】语录库 {len(corpus)} 条(全部来自本人手写的旧回复) | "
          f"账号 {(c.get_session().get('user') or {}).get('username')} | "
          f"今日回复 {st.get('daily', {}).get('replies', 0)}/{MAX_REPLIES_PER_DAY}")
    print("=" * 70)
    n = 0
    for it in items:
        tid = it.get("id")
        title = it.get("title", "")
        excerpt = strip_html(it.get("excerpt") or "")[:120]
        if not tid or not AI_RE.search(title + " " + excerpt):
            continue
        done_here = my_reply_count_in_topic(c, tid, uid)
        if done_here >= MAX_REPLIES_PER_TOPIC:
            print(f"  [已刷过] 《{title[:36]}》")
            continue
        n += 1
        print(f"\n  ── [{n}] 《{title[:40]}》 by {(it.get('author') or {}).get('username')}")
        print(f"     topicId={tid}")
        print(f"     摘要: {excerpt}")
        try:
            fp = (c.get_topic(tid) or {}).get("firstPost") or {}
            root = strip_html(fp.get("cookedHtml") or "")[:220].replace("\n", " / ")
            if root:
                print(f"     首楼: {root}")
        except Exception:
            pass
        try:
            posts = posts_of(c, tid, limit=40)
            floors = sorted({p.get("postNumber") for p in posts if (p.get("postNumber") or 0) >= 2})
            print(f"     楼中楼可选: " + (",".join(str(f) for f in floors[:5]) if floors else "无(只有首楼)"))
        except Exception:
            floors = []
        cands = match_quotes(corpus, title, excerpt, topn=3)
        if cands:
            print("     关键词候选(仅提示, 语义要自己判断):")
            for q in cands:
                print(f"       {q['id']} [{q['score']}分] {q['text'][:56]}  <<《{q['topicTitle'][:16]}》")
        else:
            print("     (关键词没匹配到合适的语录 —— 可能真没有, 就别发)")
    print("\n" + "-" * 70)
    print(f"  待选 {n} 个帖子 | 语录库 {len(corpus)} 条, 完整列表:")
    for q in corpus:
        print(f"    {q['id']} ({q['len']}字) 《{q['topicTitle'][:20]}》 {q['text'][:56]}")
    print("-" * 70)
    print(f"  发法: pickcat_bot.py --account aibot quip --topic-id <id> --reply-to <楼> --quote <语录id>")
    print("=" * 70)


def cmd_quip(c, args):
    """在指定帖子里发**本人旧回复里的语录**(或自定义正文)。全部护栏照常生效。"""
    import types as _types

    from pickcat_quotes import load_corpus
    body = args.body or ""
    if args.quote:
        corpus = load_corpus(QUOTE_UID)
        hit = next((q for q in corpus if q["id"] == args.quote), None)
        if not hit:
            print(f"找不到语录 {args.quote}; 先跑 quip-scan 看语录列表", file=sys.stderr)
            sys.exit(2)
        body = hit["text"]
        if args.attrib and hit.get("url"):
            body += f"\n（引自我的旧回复：{hit['url']}）"
    if not body:
        print("没有内容: 给 --quote <语录id> 或 --body", file=sys.stderr)
        sys.exit(2)
    if args.dry_run:
        print(f"[dry-run] {args.topic_id} 的 #{args.reply_to} 楼:")
        print(f"          {body}")
        return
    a = _types.SimpleNamespace(topic_id=args.topic_id, reply_to=args.reply_to,
                               body=body, title=args.title or "", notify=None)
    return cmd_reply(c, a)


def cmd_state(c, args):
    st = load_state()
    d = daily_guard(st, "replies")  # 跨天自动清零, 别显示昨天的残留
    save_state(st)
    print(f"今日({d.get('date', '-')}): 评论 {d.get('comments', 0)}/{MAX_COMMENTS_PER_DAY}, "
          f"回复 {d.get('replies', 0)}/{MAX_REPLIES_PER_DAY}")
    print(f"关注列表 ({len(st.get('watched', {}))}):")
    for tid, info in st.get("watched", {}).items():
        print(f"  - {info.get('title', '')[:40]}  {tid}  last_seen={info.get('last_seen')}")


def cmd_reconcile(c, args):
    """独立全量对账: 用**第二套判据**复核"到底还有没有人回我"。

    为什么需要它: notify / check 都是"单侧视图", 各自有盲区 ——
    notify 依赖 notified_handled 标记 + 7 天时间窗, check 依赖 last_seen 水位。
    历史上多次出现"报告 0 条待回复, 实际有人回了没被看见"。

    本命令**绕开那两个状态**, 只用一个客观判据: **那个楼层我是否已经接过话**
    (服务端我的楼层 replyToPostNumber + 本地账本 replyTo, 见 answered_floor_numbers)。
    两路数据源取并集:
      (1) 通知侧: 全量翻页拉 REPLY_CREATED, 逐条查是否已回过;
      (2) 帖子侧: 扫我的全部主题 + 所有关注帖, 找"父楼层作者是我"的楼。
    只读, 不写任何状态 —— 用来验证 notify/check 的结论可不可信。
    """
    st = load_state()
    seen = set(st.get("notified_handled") or [])
    watched = st.get("watched") or {}
    uid = client_uid(c)

    # ---------- (1) 通知侧 ----------
    items = _fetch_notifications(c)
    by_floor = {}
    for it in items:
        if it.get("type") != "REPLY_CREATED":
            continue
        d = it.get("details") or {}
        topic = d.get("topic") or {}
        post = d.get("post") or {}
        pn = post.get("postNumber")
        tid = topic.get("id")
        if not tid or pn is None:
            continue
        by_floor.setdefault((tid, int(pn)), []).append({
            "nid": it.get("id"),
            "actor": (d.get("actor") or {}).get("username"),
            "title": topic.get("title"),
            "at": it.get("createdAt"),
            "seen": it.get("id") in seen,
        })

    ans_cache = {}
    merged = {}
    for (tid, pn), rows in by_floor.items():
        if tid not in ans_cache:
            try:
                ans_cache[tid] = answered_floor_numbers(c, tid, uid)
            except Exception:
                ans_cache[tid] = set()
        if pn in ans_cache[tid]:
            continue  # 我已经接过话了
        r = rows[0]
        merged[(tid, pn)] = {
            "src": "通知", "topicId": tid, "pn": pn, "actor": r["actor"],
            "title": r["title"], "at": r["at"], "nid": r["nid"],
            "seen": r["seen"], "recent": _is_recent(
                next((x for x in items if x.get("id") == r["nid"]), {}),
                REPLY_HORIZON_DAYS),
        }

    # ---------- (2) 帖子侧 ----------
    topics = {}
    try:
        for t in my_topics(c, uid, limit=50):
            topics[t["id"]] = t.get("title", "")
    except PickcatError as e:
        print(f"# 拉取我的帖子失败: {e}", file=sys.stderr)
    for tid, info in watched.items():
        topics.setdefault(tid, info.get("title", ""))

    orphan = 0
    for tid, title in topics.items():
        try:
            posts = posts_of(c, tid)
            meta = c.get_topic(tid)
        except PickcatError as e:
            print(f"# 读帖失败 {tid}: {e}", file=sys.stderr)
            continue
        topic_author = (meta.get("author") or {}).get("id")
        author_by_pn = {1: topic_author}
        for it in posts:
            author_by_pn[int(it.get("postNumber") or 0)] = (it.get("author") or {}).get("id")
        answered = answered_floor_numbers(c, tid, uid)
        pns = set(author_by_pn.keys())
        for it in posts:
            pn = int(it.get("postNumber") or 0)
            a = it.get("author") or {}
            if a.get("id") == uid:
                continue
            rtp = it.get("replyToPostNumber")
            if rtp is not None and int(rtp) > 1 and int(rtp) not in pns:
                orphan += 1  # 父楼层被删 -> 无法判定归属, 计数备查
            parent = author_by_pn.get(int(rtp)) if rtp is not None else None
            if parent != uid or pn in answered:
                continue
            merged.setdefault((tid, pn), {
                "src": "帖子", "topicId": tid, "pn": pn, "actor": a.get("username"),
                "title": title, "rtp": rtp, "at": it.get("createdAt"),
                "text": strip_html(it.get("cookedHtml") or "")[:200],
                "seen": None, "recent": _is_recent(it, REPLY_HORIZON_DAYS),
            })

    # ---------- 输出 ----------
    fresh = [r for r in merged.values() if r.get("recent")]
    stale = [r for r in merged.values() if not r.get("recent")]
    print("=" * 62)
    print(f"[对账] 通知全量 {len(items)} 条 | 扫帖 {len(topics)} 个 | "
          f"父楼层缺失(孤儿) {orphan} 条")
    print(f"[未回过合计] {len(merged)} 条 —— 其中时间窗内 {len(fresh)} 条, "
          f"超出 {REPLY_HORIZON_DAYS} 天窗 {len(stale)} 条")
    if fresh:
        print("[时间窗内 — 需要处理]")
        for r in sorted(fresh, key=lambda x: str(x.get("at") or "")):
            print(f"  #{r['pn']} 来自 {r.get('actor')} [{r['src']}] 《{r.get('title')}》")
            print(f"    topicId={r['topicId']}")
            if r.get("nid"):
                print(f"    notificationId={r['nid']}")
            if r.get("rtp") is not None:
                print(f"    replyToPostNumber={r['rtp']}")
            if r.get("text"):
                print(f"    内容: {r['text']}")
            print(f"    时间: {r.get('at')}")
    else:
        print("[时间窗内] 无 —— 确认没有待回复 (两路独立判据一致)")
    if stale and args.verbose:
        print(f"[超窗, 仅备查] {len(stale)} 条:")
        for r in sorted(stale, key=lambda x: str(x.get("at") or "")):
            print(f"  #{r['pn']} 来自 {r.get('actor')} 《{r.get('title')}》 {r.get('at')}")
    print("=" * 62)


def cmd_audit(c, args):
    """审计: API 调用量(以本地账本为准) + 服务端水印是否还在 + 序号缺口。"""
    from pickcat_client import wm_decode, wm_strip, ledger_read

    st = load_state()
    uid = client_uid(c)
    ledger = ledger_read()
    seqs = [int(r.get("seq") or 0) for r in ledger]
    gaps = [n for n in range(1, max(seqs) + 1) if n not in seqs] if seqs else []

    print("=" * 62)
    print("【调用量审计】本地账本 (权威)")
    print(f"  API 写操作总次数 : {len(ledger)}")
    topics = sum(1 for r in ledger if r.get("kind") == "topic")
    replies = sum(1 for r in ledger if r.get("kind") == "reply")
    print(f"    其中 发帖 {topics} / 回帖 {replies}")
    print(f"  序号范围         : {min(seqs) if seqs else '-'} ~ {max(seqs) if seqs else '-'}")
    if gaps:
        print(f"  ⚠ 序号缺口        : {gaps}  (这些序号没进账本: 调用失败/未发起)")
    else:
        print("  序号无缺口        : 每次调用都有账")
    if ledger:
        print("  最近几条:")
        for r in ledger[-args.tail:]:
            what = r.get("title") or r.get("text") or ""
            print(f"    seq={r.get('seq'):<4} {r.get('at')}  {r.get('kind'):<6} "
                  f"{r.get('status', ''):<16} {what[:34]}")

    # 交叉验证: 抽查账本里的 postId, 看服务端存的原文里水印还在不在
    print("-" * 62)
    sample = ledger if args.all else ledger[-args.tail:]
    ok = bad = 0
    for r in sample:
        pid = r.get("postId")
        if not pid:
            continue
        try:
            d = c._request("GET", f"/api/v1/posts/{pid}")
            md = ((d or {}).get("request") or {}).get("markdown", "") or ""
            found = wm_decode(md)
            if found and found[0][0] == int(r.get("seq") or 0):
                ok += 1
            else:
                bad += 1
                print(f"  ✗ seq={r.get('seq')} 水印不符: {found}")
        except PickcatError as e:
            bad += 1
            print(f"  ✗ seq={r.get('seq')} 查询失败: {e}")
    print(f"  服务端水印抽查 ({len(sample)} 条): 一致 {ok} / 异常 {bad}")

    # 反向扫描: 列表里带水印但不在账本的 (比如用别的工具发的)
    print("-" * 62)
    orphan = []
    for tid, info in st.get("watched", {}).items():
        try:
            items = posts_of(c, tid)
        except PickcatError:
            continue
        for it in items:
            if (it.get("author") or {}).get("id") != uid:
                continue
            found = wm_decode(it.get("cookedHtml") or "")
            for s, _, _ in found:
                if s not in seqs:
                    orphan.append((s, info.get("title", "")[:26]))
    if orphan:
        print(f"  ⚠ 列表里发现账本外的带水印内容 {len(orphan)} 条 (可能是别处发的):")
        for s, t in orphan[:6]:
            print(f"    - seq={s}  《{t}》")
    else:
        print("  无账本外的带水印内容")
    print("=" * 62)


def cmd_verify(c, args):
    """验证水印能不能在服务端存活: 看最近一条自己发的内容里能不能解出序号。"""
    from pickcat_client import wm_decode, wm_strip

    st = load_state()
    uid = client_uid(c)
    newest = None
    for tid, info in st.get("watched", {}).items():
        try:
            items = posts_of(c, tid)
        except PickcatError:
            continue
        for it in items:
            if (it.get("author") or {}).get("id") != uid:
                continue
            if newest is None or (it.get("postNumber") or 0) > newest[0]:
                newest = (it.get("postNumber") or 0, tid, it)
    if not newest:
        print("没找到我自己发的内容")
        return
    pn, tid, it = newest
    html = it.get("cookedHtml") or ""
    found = wm_decode(html)
    print(f"最新一条: topic={tid} #{pn}")
    print(f"  原文尾部 repr: {repr(html[-30:])}")
    print(f"  解出水印: {found if found else '无 —— 水印被服务端清洗掉了!'}")
    print(f"  去水印后: {wm_strip(html)[:80]}")


def main():
    ap = argparse.ArgumentParser(description="Pickcat 半自动运营助手")
    ap.add_argument("--account", default=None,
                    help="账号档案名: 读 accounts/<名字>.env 的 cookie, 状态文件也按它隔离(.state/)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check", help="扫描待回复 + AI 帖候选")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("reply", help="回一句")
    p.add_argument("--topic-id", required=True)
    p.add_argument("--reply-to", dest="reply_to", type=int, default=None,
                   help="回复的楼层号; 1 或省略 = 顶层评论")
    p.add_argument("--body", required=True)
    p.add_argument("--title", default="")
    p.add_argument("--notify", default=None,
                   help="回完后把这条通知标记为已处理 (配合 notify 命令)")

    p = sub.add_parser("comment", help="评一条顶层评论")
    p.add_argument("--topic-id", required=True)
    p.add_argument("--body", required=True)
    p.add_argument("--title", default="")

    p = sub.add_parser("watch", help="关注某帖")
    p.add_argument("--topic-id", required=True)
    p.add_argument("--last-seen", dest="last_seen", type=int, default=None)
    p.add_argument("--title", default="")

    sub.add_parser("state", help="打印状态")
    p = sub.add_parser("reconcile", help="独立全量对账: 用第二套判据复核'还有没有人回我'")
    p.add_argument("--verbose", action="store_true", help="同时列出超出时间窗的条目")
    p = sub.add_parser("audit", help="水印审计: API 调用量(账本) + 服务端水印核对 + 序号缺口")
    p.add_argument("--tail", type=int, default=5, help="抽查最近几条 (默认5)")
    p.add_argument("--all", action="store_true", help="全量抽查")
    sub.add_parser("verify", help="验证水印能否在服务端存活")
    sub.add_parser("refresh", help="复查未完成投稿的审核状态 + 更新配额")
    sub.add_parser("quota", help="查看配额限制与恢复时间")
    p = sub.add_parser("quip-scan", help="选材: AI 帖 + 正文 + 本人语录库")
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--rebuild", action="store_true", help="重建语录库")
    p = sub.add_parser("quip", help="在指定帖子里发固定梗(护栏照常生效)")
    p.add_argument("--topic-id", required=True)
    p.add_argument("--reply-to", dest="reply_to", type=int, default=2,
                   help="回复到第几楼; 默认 2(楼中楼, 遵守'每帖顶层评论=0')")
    p.add_argument("--body", default="", help="直接给正文(不给就用 --quote)")
    p.add_argument("--quote", default="", help="语录 id (q001...), 从语录库里挑")
    p.add_argument("--attrib", action="store_true", help="末尾附上原回复链接(标明是引用本人旧回复)")
    p.add_argument("--title", default="")
    p.add_argument("--dry-run", action="store_true")
    sub.add_parser("limits", help="摊开限制总表: 平台上限/机器人份额/号主余量/今日用量")
    p = sub.add_parser("notify", help="列出谁回了我(读通知, 比扫帖子准)")
    p.add_argument("--limit", type=int, default=30)

    args = ap.parse_args()
    if args.account:
        os.environ["PICKCAT_PROFILE"] = args.account
        print(f"[账号档案] {args.account}")
    reload_limits()  # 档案切换后重新读上限(主号与小号可以各一套)
    c = build_client()
    try:
        {
            "check": cmd_check,
            "reply": cmd_reply,
            "comment": cmd_comment,
            "watch": cmd_watch,
            "state": cmd_state,
            "reconcile": cmd_reconcile,
            "audit": cmd_audit,
            "verify": cmd_verify,
            "refresh": cmd_refresh,
            "quota": cmd_quota,
        "quip-scan": cmd_quip_scan,
        "quip": cmd_quip,
        "limits": cmd_limits,
        "notify": cmd_notify,
        }[args.cmd](c, args)
    except PickcatError as e:
        # 服务端整体不可用时给一句人话, 而不是甩 traceback
        from pickcat_client import is_service_down

        if is_service_down(e):
            outage_record(False)
            print("=" * 58)
            print("⛔ 服务不可用: 上游 API 返回 502/超时。")
            print("   前端壳还能开, 但 /api/v1/* 全部取不到数据 —— 服务端问题, 与凭证/配额无关。")
            print("   本轮不执行任何写操作, 等服务端恢复后自动继续。")
            print("=" * 58)
            sys.exit(1)
        print(f"失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
