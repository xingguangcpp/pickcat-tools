#!/usr/bin/env python3
"""
pickcat_quotes.py — 语录库: 从**用户自己写过的回复**里挖"语录", 给 AI 相关的帖子挑合适的那句。

定位(重要): 这不是"让 AI 写内容", 而是让 AI **做检索与选材** ——
被发出去的每个字都是本人以前亲手写的, AI 只负责"哪一句配得上这个帖子"。
这正好落在社区认可的「AI 辅助人写」里。

关键过滤: **只收人写的回复**。
带隐形水印的 = 当时是 AI 写的, 不能进语录库 ——
把被处罚过的 AI 内容换个号再发一遍, 等于把违规内容重新上架。
"""

import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from pickcat_client import wm_decode  # noqa: E402

QUOTE_MIN = 8          # 太短没信息量
QUOTE_MAX = 200        # 太长就不是"语录"了, 而且长回复离不开上下文
MIN_SCORE = 4          # 低于这个分就算"没有明显合适的语录" —— 宁可不发, 也不硬凑
CORPUS_FILE = ".quotes_<uid>.json"

# 社区/技术域的关键词(命中即视为同域)。从社区自己的用词里攒的。
DOMAIN_WORDS = [
    # AI 相关
    "ai", "agi", "llm", "gpt", "agent", "模型", "大模型", "智能体", "提示词", "prompt",
    "深度学习", "机器学习", "神经网络", "chatbot", "自动化", "机器人", "豆包", "训练",
    "boxplay", "pikagent", "kn界", "kitten", "coco",
    # 社区/行为
    "回复", "楼", "盖楼", "水印", "禁言", "封号", "巡查", "公告", "社区", "站长", "判据",
    "人类", "写作", "文章", "内容", "划走", "看腻", "刷屏", "扰民",
    # 技术
    "接口", "api", "cookie", "token", "限流", "配额", "额度", "异步", "审核",
    "内存", "显存", "显卡", "核显", "延迟", "卡顿", "帧率", "渲染", "webgl", "服务器",
    "加速", "steam", "编译", "c++", "cpp", "python", "源码", "代码", "积木", "克隆体",
    "调试", "算法", "洛谷", "题", "贪心", "位运算", "联机", "插件", "教程", "作品", "关卡",
]


def _strip_html(html):
    return re.sub(r"<[^>]+>", "", html or "").strip()


def _is_codeish(text):
    """像代码/贴图/堆砌的回复, 不适合当语录。"""
    if not text:
        return True
    if "function" in text or "const " in text or "def " in text:
        return True
    if text.count("{") > 3 or text.count("}") > 3:
        return True
    if text.count("\n") > 4:
        return True
    if text.count("//") > 3:
        return True
    # 几乎没有可读文字(纯符号/表情)
    readable = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", text)
    return len(readable) < 6


def _norm(text):
    return re.sub(r"[\s\u200b\u2060-\u2064\u200d]+", "", text)


def fetch_own_replies(c, uid, max_pages=12):
    """翻页拉完这个账号自己的回复 (只读)。"""
    out = []
    cursor = None
    for _ in range(max_pages):
        q = "/api/v1/users/%s/posts?role=reply&limit=50" % uid
        if cursor:
            q += "&cursor=" + cursor
        try:
            data = c._request("GET", q)
        except Exception as e:
            print(f"  [语录] 拉取失败(第 {len(out)} 条后): {str(e)[:80]}", file=sys.stderr)
            break
        items = (data.get("items") if isinstance(data, dict) else data) or []
        if not items:
            break
        out.extend(items)
        pi = data.get("pageInfo") or {}
        cursor = pi.get("nextCursor")
        if not pi.get("hasNextPage") or not cursor:
            break
    return out


def build_corpus(c, uid, verbose=True):
    """构造语录库: 只收**人写的**、能独立成句的回复。"""
    replies = fetch_own_replies(c, uid)
    seen = set()
    corpus = []
    skipped = {"ai": 0, "code": 0, "short": 0, "long": 0, "dup": 0, "multiline": 0}
    for it in replies:
        text = _strip_html(it.get("cookedHtml"))
        if not text:
            continue
        if wm_decode(text):
            skipped["ai"] += 1          # 当时是 AI 写的 -> 不能当语录
            continue
        if _is_codeish(text):
            skipped["code"] += 1
            continue
        if "\n" in text:
            # 带换行的是表单/模板(比如"自我介绍:introduction"那种), 不是语录
            skipped["multiline"] += 1
            continue
        if len(text) < QUOTE_MIN:
            skipped["short"] += 1
            continue
        if len(text) > QUOTE_MAX:
            skipped["long"] += 1
            continue
        key = _norm(text)
        if key in seen:
            skipped["dup"] += 1
            continue
        seen.add(key)
        topic = it.get("topic") or {}
        corpus.append({
            "id": "q%03d" % (len(corpus) + 1),
            "text": text,
            "len": len(text),
            "topicId": topic.get("id"),
            "topicTitle": topic.get("title") or "",
            "postNumber": it.get("postNumber"),
            "url": ("https://pickcat.dao3.fun/forum/post/%s" % topic.get("id")) if topic.get("id") else "",
        })
    if verbose:
        print(f"  [语录] 扫描 {len(replies)} 条自己的回复 -> 可用语录 {len(corpus)} 条"
              f" (排除: AI写的 {skipped['ai']} / 代码堆 {skipped['code']} / 太短 {skipped['short']}"
              f" / 太长 {skipped['long']} / 多行模板 {skipped['multiline']} / 重复 {skipped['dup']})", file=sys.stderr)
    return corpus


def save_corpus(uid, corpus):
    import json
    p = HERE / CORPUS_FILE.replace("<uid>", uid)
    p.write_text(json.dumps({"generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
                             "uid": uid, "quotes": corpus}, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return p


def load_corpus(uid):
    import json
    p = HERE / CORPUS_FILE.replace("<uid>", uid)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("quotes") or []
    except Exception:
        return []


def _grams(text, n=2):
    t = re.sub(r"[^\w\u4e00-\u9fff]+", "", (text or "").lower())
    return {t[i:i + n] for i in range(len(t) - n + 1)}


def _domain_hits(text):
    low = (text or "").lower()
    return [w for w in DOMAIN_WORDS if w in low]


def match_quotes(corpus, post_title, post_excerpt="", topn=3):
    """给一个帖子挑最合适的语录, 附上"为什么合适"。

    打分维度:
      - 同域关键词命中(题与语录共有的领域词) —— 最强信号
      - 二元字组重合(中文近似相似度)
      - 语录原帖标题与当前帖标题的相似度 —— 同一话题下说过的话最贴
    """
    post_text = (post_title or "") + " " + (post_excerpt or "")
    post_grams = _grams(post_text)
    post_words = set(_domain_hits(post_text))
    out = []
    for q in corpus:
        # 关键: 字组重合只算**语录正文**。
        # 早先把"原帖标题"也算进去, 结果"服务器炸了..?为什么进不去?"这种正文毫无关系的
        # 句子, 因为原帖标题一样就拿了 49 分 —— 标题像 != 这句话配得上这个帖子。
        qg = _grams(q["text"])
        overlap = len(post_grams & qg)
        words = set(_domain_hits(q["text"]))
        common_words = sorted(post_words & words)
        topic_words = set(_domain_hits(q.get("topicTitle", "")))
        same_topic = bool(topic_words & post_words)
        score = overlap + 3 * len(common_words) + (2 if same_topic else 0)
        if score < MIN_SCORE:
            continue
        reasons = []
        if common_words:
            reasons.append("同域词:" + "/".join(common_words[:4]))
        if same_topic:
            reasons.append("原帖同话题")
        if overlap:
            reasons.append(f"字组重合 {overlap}")
        out.append({**q, "score": score, "reason": " / ".join(reasons)})
    out.sort(key=lambda x: -x["score"])
    return out[:topn]


def main():
    import argparse
    ap = argparse.ArgumentParser(description="语录库: 挖自己写过的回复, 给 AI 帖挑合适的语录")
    ap.add_argument("--uid", default="01a0d338-72f2-731e-a0c5-10724fc89813", help="语录来源账号")
    ap.add_argument("--save", action="store_true", help="保存语录库到本地")
    ap.add_argument("--show", action="store_true", help="打印全部语录")
    args = ap.parse_args()
    from pickcat_bot import build_client
    c = build_client()
    corpus = build_corpus(c, args.uid)
    if args.save:
        p = save_corpus(args.uid, corpus)
        print(f"  [语录] 已保存 -> {p.name}")
    if args.show:
        for q in corpus:
            print(f"  {q['id']} ({q['len']}字) 《{q['topicTitle'][:22]}》 {q['text']}")


if __name__ == "__main__":
    main()
