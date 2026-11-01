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
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from pickcat_client import PickcatClient, PickcatError  # noqa: E402

STATE_FILE = HERE / ".bot_state.json"
ENV_FILE = HERE / ".env"

# 每帖顶层评论上限 (用户定的规矩)
MAX_TOP_LEVEL = 3
# 每日上限, 防刷屏
MAX_COMMENTS_PER_DAY = int(os.environ.get("PICKCAT_BOT_MAX_COMMENTS_PER_DAY", "4"))
MAX_REPLIES_PER_DAY = int(os.environ.get("PICKCAT_BOT_MAX_REPLIES_PER_DAY", "8"))
# 两次写操作最小间隔(秒)
WRITE_INTERVAL = float(os.environ.get("PICKCAT_BOT_WRITE_INTERVAL", "8"))
# feed 里最多看多少帖用于找 AI 候选
FEED_SCAN = int(os.environ.get("PICKCAT_BOT_FEED_SCAN", "30"))
# 最多给几个候选
MAX_CANDIDATES = 5

AI_RE = re.compile(
    r"\bai\b|\bagent\b|\bllm\b|\bgpt\b|\bagi\b|大模型|智能体|提示词|prompt|"
    r"深度学习|神经网络|机器学习|openai|claude|deepseek|chatbot|自动化|机器人",
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


def build_client():
    env = load_env()
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
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(st):
    STATE_FILE.write_text(
        json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8"
    )


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


def posts_of(c, topic_id, limit=50):
    """返回该帖所有楼层(含楼中楼)。

    注意: GET /api/v1/topics/{id}/posts 返回的 items 只有顶层回复,
    楼中楼藏在每层的 children 数组里 (replyToPostNumber 指向父楼层)。
    不摊平 children 会漏掉"别人回复了我在楼中楼里的话"这种最典型的回帖。
    """
    data = c.get_topic_posts(topic_id, limit=limit)
    items = (data.get("items") if isinstance(data, dict) else data) or []
    return flatten_posts(items)


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


def my_top_level_count(c, topic_id, uid):
    """我在该帖发过的顶层评论数 (顶层 = replyToPostNumber 指向首楼或为空)。"""
    n = 0
    for it in posts_of(c, topic_id):
        if (it.get("author") or {}).get("id") != uid:
            continue
        rtp = it.get("replyToPostNumber")
        if rtp in (None, 1):
            n += 1
    return n


def daily_guard(st, key):
    """返回 (今天计数 dict, 是否超限)。跨天自动清零。"""
    d = st.setdefault("daily", {})
    if d.get("date") != today_key():
        d["date"] = today_key()
        d["comments"] = 0
        d["replies"] = 0
    return d


# ---------------------------------------------------------------- check
def cmd_check(c, args):
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
        last = int(info.get("last_seen", 0))
        for it in items:
            pn = int(it.get("postNumber") or 0)
            if pn <= last:
                continue
            a = it.get("author") or {}
            if a.get("id") == uid:
                continue  # 自己发的跳过
            pending.append(
                {
                    "topicId": tid,
                    "title": info.get("title", ""),
                    "postNumber": pn,
                    "replyToPostNumber": it.get("replyToPostNumber"),
                    "author": a.get("username"),
                    "authorId": a.get("id"),
                    "text": strip_html(it.get("cookedHtml") or it.get("markdown"))[:300],
                }
            )
        # 注意: 这里不推进 last_seen —— 只有真正回过 (reply/comment) 才记账,
        # 这样本轮没处理掉的回复, 下一轮还会被拾起来, 不会被永久跳过。

        # 截断感知: 服务端可能只返回部分回复 (repliesTruncated)。
        # 此时"看不到"不等于"没人回", 必须显式报出来, 不能让 Agent 误判。
        try:
            meta = c.get_topic(tid)
            rc = meta.get("replyCount")
            trunc = meta.get("repliesTruncated")
            # 注意: repliesTruncated 在这 API 上基本常驻 true, 不能单独当告警依据,
            # 真正可信的信号是 "replyCount 变大了, 但列表里没出现对应的新楼层"。
            prev_rc = info.get("reply_count")
            new_items = [p for p in pending if p["topicId"] == tid]
            if rc is not None and prev_rc is not None and rc > prev_rc:
                if not new_items:
                    anomalies.append(
                        f"《{info.get('title','')[:24]}》replyCount {prev_rc}->{rc} 但列表里没有新楼层 "
                        f"(repliesTruncated={trunc}) —— 可能有未显示的回复, 别当成'没人回'"
                    )
                else:
                    info["reply_count"] = rc
            elif rc is not None:
                info["reply_count"] = rc
            if rc is not None and rc > len(items):
                anomalies.append(
                    f"《{info.get('title','')[:24]}》replyCount={rc} > 返回 {len(items)} 条 "
                    f"(列表不完整)"
                )
            la = meta.get("lastActivityAt")
            prev_la = info.get("last_activity_at")
            if la and prev_la and la > prev_la and not any(
                p["topicId"] == tid for p in pending
            ):
                anomalies.append(
                    f"《{info.get('title','')[:24]}》lastActivityAt 有更新但列表里没有新楼层 "
                    f"(列表可能被截断)"
                )
            if la:
                info["last_activity_at"] = la
        except PickcatError:
            pass
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
            candidates.append(
                {
                    "topicId": tid,
                    "title": title,
                    "author": (it.get("author") or {}).get("username"),
                    "excerpt": excerpt,
                    "myTopLevel": my_top_level_count(c, tid, uid),
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
    if anomalies:
        print(f"[⚠ 数据异常] {len(anomalies)} 条 —— 列表可能被服务端截断, 别当成'没人回'")
        for a in anomalies:
            print("  - " + a)
        print("-" * 60)
    print(f"[待回复] {len(pending)} 条")
    for p in pending:
        print(f"  帖子: {p['title']}")
        print(f"    topicId={p['topicId']}  楼层 #{p['postNumber']} "
              f"(replyTo={p['replyToPostNumber']})  来自 {p['author']}")
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
    st = load_state()
    uid = client_uid(c)
    d = daily_guard(st, "replies")

    # 护栏 1: 顶层评论每帖 <= 3
    if args.reply_to in (0, 1, None):
        cnt = my_top_level_count(c, args.topic_id, uid)
        if cnt >= MAX_TOP_LEVEL:
            print(f"[护栏] 该帖我的顶层评论已 {cnt} 条 (上限 {MAX_TOP_LEVEL}), "
                  f"拒绝再发顶层评论。楼中楼回复不受限。", file=sys.stderr)
            sys.exit(3)

    # 护栏 2: 每日回复上限
    if d["replies"] >= MAX_REPLIES_PER_DAY:
        print(f"[护栏] 今日回复已达上限 {MAX_REPLIES_PER_DAY}, 跳过。", file=sys.stderr)
        sys.exit(3)

    resp = c.create_reply(
        args.topic_id, args.body, reply_to_post_number=args.reply_to
    )
    d["replies"] += 1
    # 顺手关注这个帖 (以后有人回我继续跟)
    w = st.setdefault("watched", {})
    if args.topic_id not in w:
        w[args.topic_id] = {"title": args.title or "", "last_seen": 0}
    w[args.topic_id]["last_seen"] = max_post_number(c, args.topic_id)
    save_state(st)
    print(json.dumps(resp, ensure_ascii=False))
    print("[ok] 已提交, 状态: " + str(resp.get("status")), file=sys.stderr)


# ---------------------------------------------------------------- comment
def cmd_comment(c, args):
    """在已有帖子下评一条顶层评论 (受每帖<=3 与每日上限约束)。"""
    st = load_state()
    uid = client_uid(c)
    d = daily_guard(st, "comments")

    cnt = my_top_level_count(c, args.topic_id, uid)
    if cnt >= MAX_TOP_LEVEL:
        print(f"[护栏] 该帖我的顶层评论已 {cnt} 条 (上限 {MAX_TOP_LEVEL}), 拒绝。",
              file=sys.stderr)
        sys.exit(3)
    if d["comments"] >= MAX_COMMENTS_PER_DAY:
        print(f"[护栏] 今日评论已达上限 {MAX_COMMENTS_PER_DAY}, 跳过。", file=sys.stderr)
        sys.exit(3)

    resp = c.create_reply(args.topic_id, args.body, reply_to_post_number=1)
    d["comments"] += 1
    w = st.setdefault("watched", {})
    if args.topic_id not in w:
        w[args.topic_id] = {"title": args.title or "", "last_seen": 0}
    w[args.topic_id]["last_seen"] = max_post_number(c, args.topic_id)
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


def cmd_state(c, args):
    st = load_state()
    d = st.get("daily", {})
    print(f"今日({d.get('date', '-')}): 评论 {d.get('comments', 0)}/{MAX_COMMENTS_PER_DAY}, "
          f"回复 {d.get('replies', 0)}/{MAX_REPLIES_PER_DAY}")
    print(f"关注列表 ({len(st.get('watched', {}))}):")
    for tid, info in st.get("watched", {}).items():
        print(f"  - {info.get('title', '')[:40]}  {tid}  last_seen={info.get('last_seen')}")


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
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check", help="扫描待回复 + AI 帖候选")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("reply", help="回一句")
    p.add_argument("--topic-id", required=True)
    p.add_argument("--reply-to", dest="reply_to", type=int, default=None,
                   help="回复的楼层号; 1 或省略 = 顶层评论")
    p.add_argument("--body", required=True)
    p.add_argument("--title", default="")

    p = sub.add_parser("comment", help="评一条顶层评论")
    p.add_argument("--topic-id", required=True)
    p.add_argument("--body", required=True)
    p.add_argument("--title", default="")

    p = sub.add_parser("watch", help="关注某帖")
    p.add_argument("--topic-id", required=True)
    p.add_argument("--last-seen", dest="last_seen", type=int, default=None)
    p.add_argument("--title", default="")

    sub.add_parser("state", help="打印状态")
    p = sub.add_parser("audit", help="水印审计: API 调用量(账本) + 服务端水印核对 + 序号缺口")
    p.add_argument("--tail", type=int, default=5, help="抽查最近几条 (默认5)")
    p.add_argument("--all", action="store_true", help="全量抽查")
    sub.add_parser("verify", help="验证水印能否在服务端存活")

    args = ap.parse_args()
    c = build_client()
    {
        "check": cmd_check,
        "reply": cmd_reply,
        "comment": cmd_comment,
        "watch": cmd_watch,
        "state": cmd_state,
        "audit": cmd_audit,
        "verify": cmd_verify,
    }[args.cmd](c, args)


if __name__ == "__main__":
    main()
