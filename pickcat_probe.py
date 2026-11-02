#!/usr/bin/env python3
"""
pickcat_probe.py — API 巡检器: 持续探测接口表面, 自动记录变化

定位: 手写的接口清单和"坑表"会随服务端更新而失真。这个脚本把它变成**活的**——
每隔一段时间跑一轮, 自动:
  1. 探测全部已知端点 (只读! 绝不写, 不消耗发帖额度)
  2. 记录每个探测的: HTTP 状态 / 耗时 / 错误码 / 响应字段结构 / 条目数
  3. 与上一份快照对比, 识别: 新增端点 / 消失端点 / 状态变化 / 结构变化 / 错误码变化
  4. 写 docs/api-snapshot.json   (当前状态, 机器可读)
     写 docs/API-STATUS.md       (当前状态, 人看的表格, 每次重新生成)
     追加 docs/API-CHANGELOG.md  (变化流水, 只在有变化时追加)

用法:
  python pickcat_probe.py             # 跑一轮
  python pickcat_probe.py --json      # 额外打印机器可读结果
  python pickcat_probe.py --always    # 即使没变化也记一条(记录"服务可用"这件事)
  python pickcat_probe.py --list      # 只看探测清单

设计要点:
  - 服务不可用时 (502/超时) 不空转: 记一条 "服务不可用" 后安静退出, 不重复撞墙。
  - 探测清单只含 GET —— 写接口的"额度/审核/水印"由 pickcat_bot 的 refresh 负责。
  - 400 与 404 要分清: 400 = 端点存在但参数不合法 (可继续深挖), 404 = 端点不存在。
    Zod 的 details 会泄露期望的 schema, 这是重要的探索线索, 所以一并记下来。
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from pickcat_client import PickcatError, is_service_down, service_is_down, state_file  # noqa: E402
from pickcat_bot import build_client  # noqa: E402

SNAPSHOT_PATH = HERE / "docs" / "api-snapshot.json"
STATUS_MD = HERE / "docs" / "API-STATUS.md"
CHANGELOG_MD = HERE / "docs" / "API-CHANGELOG.md"

# "已发布基线" (gitignored): 记录**最后一次成功推送**时的快照。
# 为什么不能直接拿本地 docs/api-snapshot.json 当对比基线?
#   因为它是每轮都被覆盖的。如果某轮 push 失败 (token 过期/网络问题),
#   那一轮的变化会被写进本地文件, 下一轮再对比就"没变化"了 —— 变化被静默吞掉,
#   永远不会再推。拿"已发布基线"对比, 失败的那轮会在下一轮被重新检测到并重试推送。
PUBLISHED_BASELINE = HERE / ".probe_published.json"
# 已写入 CHANGELOG 的那组变化的指纹, 用来避免重试推送时重复追加同一条记录
CHANGELOG_KEY = HERE / ".probe_changelog_key"

# 探测清单: (说明, 路径, 是否公开接口)
# 公开 = 不带 cookie 也能读; 其余需要登录。
PROBES = [
    ("社区帖子 feed", "/api/v1/topics?limit=5", True),
    ("主题详情", "/api/v1/topics/{tid}", True),
    ("主题下的回复", "/api/v1/topics/{tid}/posts?limit=5", True),
    ("主题回复(按最新排序)", "/api/v1/topics/{tid}/posts?limit=5&sort=latest", True),
    ("单条回复详情", "/api/v1/posts/{pid}", True),
    ("标签列表", "/api/v1/tags", True),
    ("标签带 limit(应被拒: 未知参数)", "/api/v1/tags?limit=20", True),
    ("用户资料(含等级/统计)", "/api/v1/users/{uid}", True),
    ("投稿列表(需 contentRole+status)", "/api/v1/post-submissions?contentRole=TOPIC_REPLY&status=PENDING", False),
    ("用户回复(role=reply)", "/api/v1/users/{uid}/posts?role=reply&limit=5", True),
    ("用户回复(role 非法, 应报错)", "/api/v1/users/{uid}/posts?role=bogus", True),
    ("粉丝列表", "/api/v1/users/{uid}/followers?limit=5", True),
    ("关注列表", "/api/v1/users/{uid}/following?limit=5", True),
    ("通知列表", "/api/v1/notifications?limit=10", True),
    ("作品列表", "/api/v1/works?limit=5", True),
    ("徽章列表", "/api/v1/badges", True),
    ("表情包集列表", "/api/v1/emoji-collections", True),
    ("表情包(需 collectionId)", "/api/v1/emojis?collectionId={cid}", True),
    ("会话(需登录)", "/api/v1/session", False),
    ("我自己的主题(需登录)", "/api/v1/users/{uid}/topics?limit=5", False),
    ("投稿审核状态(需登录)", "/api/v1/post-submissions/{sid}", False),
    ("不存在的端点(应 404)", "/api/v1/__probe_nonexistent__", True),
    ("posts 列表(已知不可用)", "/api/v1/posts?limit=5", True),
    ("非法 cursor", "/api/v1/topics?cursor=abc", True),
    # --- 以下为 2026-11-03 补充: 把"服务端没说但能被问出来"的约束固化成探测项 ---
    ("主题回复 sort 非法(应泄露 sort 枚举)", "/api/v1/topics/{tid}/posts?limit=5&sort=bogus", True),
    ("用户回复加 sort(该端点无 sort 参数)", "/api/v1/users/{uid}/posts?role=reply&sort=bogus", True),
    ("通知 limit 超过上限(应泄露上限值)", "/api/v1/notifications?limit=100", True),
    ("通知 limit 取上限(应 200)", "/api/v1/notifications?limit=50", True),
    ("topics 带 sort(应支持)", "/api/v1/topics?limit=5&sort=latest", True),
    ("粉丝列表带非法 cursor(应被拒)", "/api/v1/users/{uid}/followers?limit=5&cursor=abc", True),
    ("表情包嵌套路径(已知不可用, 须用 collectionId)", "/api/v1/emoji-collections/{cid}/emojis", True),
]


# 自动生成、允许被 git 回滚的三个文件
GENERATED_DOCS = ("docs/api-snapshot.json", "docs/API-STATUS.md", "docs/API-CHANGELOG.md")


def _subst(path, ctx):
    return (path.replace("{tid}", ctx["tid"])
                .replace("{pid}", ctx["pid"])
                .replace("{uid}", ctx["uid"])
                .replace("{cid}", ctx["cid"])
                .replace("{sid}", ctx["sid"]))


def build_context(c):
    """拿几个真实 id 当探针参数 (用当前会话/我自己的内容, 不额外写任何东西)。"""
    ctx = {
        "tid": "01a195a5-b39d-7709-ad7b-0494928c6dd3",
        "pid": "01a1972c-0bef-7aa6-858c-d5938427dae4",
        "uid": "01a0d338-72f2-731e-a0c5-10724fc89813",
        "cid": "",
        "sid": "01a1956c-c398-7b35-9ff2-626cd135b1dd",
    }
    try:
        ses = c.get_session()
        u = (ses.get("user") or {})
        if u.get("id"):
            ctx["uid"] = u["id"]
    except Exception:
        pass
    try:
        cols = c._request("GET", "/api/v1/emoji-collections")
        items = (cols.get("items") if isinstance(cols, dict) else cols) or []
        if items:
            ctx["cid"] = items[0].get("id", "")
    except Exception:
        pass
    return ctx


def run_probe(c, label, path, public):
    """跑一个探测, 返回结构化结果 (不抛异常)。"""
    t0 = time.time()
    rec = {"label": label, "path": path, "public": public}
    try:
        d = c._request("GET", path)
        rec["status"] = 200
        rec["latencyMs"] = int((time.time() - t0) * 1000)
        if isinstance(d, dict):
            rec["keys"] = sorted(d.keys())[:12]
            items = d.get("items")
            rec["itemCount"] = len(items) if isinstance(items, list) else None
            pi = d.get("pageInfo")
            if isinstance(pi, dict):
                rec["hasNextPage"] = bool(pi.get("hasNextPage"))
        elif isinstance(d, list):
            rec["keys"] = ["<list>"]
            rec["itemCount"] = len(d)
        else:
            rec["keys"] = [type(d).__name__]
    except PickcatError as e:
        rec["status"] = e.status
        rec["latencyMs"] = int((time.time() - t0) * 1000)
        rec["code"] = e.code
        body = e.body if isinstance(e.body, dict) else {}
        det = body.get("details")
        if det:
            rec["details"] = json.dumps(det, ensure_ascii=False)[:220]
    except Exception as e:
        rec["status"] = -1
        rec["latencyMs"] = int((time.time() - t0) * 1000)
        rec["code"] = "EXC:" + str(e)[:80]
    return rec


def diff_snapshots(old, new):
    """对比两份快照, 返回变化列表。"""
    changes = []
    op = old.get("probes") or {}
    np_ = new.get("probes") or {}
    for key, rec in np_.items():
        prev = op.get(key)
        if prev is None:
            changes.append(f"**新增** `{key}` — {rec.get('label')} (HTTP {rec.get('status')})")
            continue
        if prev.get("status") != rec.get("status"):
            changes.append(
                f"`{key}` 状态 {prev.get('status')} → **{rec.get('status')}**"
                f" ({rec.get('code') or ''})")
        if prev.get("code") != rec.get("code") and rec.get("code"):
            changes.append(f"`{key}` 错误码 {prev.get('code')} → **{rec.get('code')}**")
        if prev.get("keys") != rec.get("keys"):
            changes.append(
                f"`{key}` 响应结构变化: {prev.get('keys')} → **{rec.get('keys')}**")
        if prev.get("itemCount") is not None and rec.get("itemCount") is not None \
                and prev.get("itemCount") != rec.get("itemCount"):
            changes.append(
                f"`{key}` 条目数 {prev.get('itemCount')} → {rec.get('itemCount')}")
        # 错误详情变化 (Zod details 会泄露 schema, 变了就值得记)
        if prev.get("details") != rec.get("details") \
                and (rec.get("details") or prev.get("details")):
            changes.append(
                f"`{key}` 错误详情变化: {str(prev.get('details'))[:110]} → "
                f"**{str(rec.get('details'))[:110]}**")
    for key, rec in op.items():
        if key not in np_:
            changes.append(f"**消失** `{key}` (原 HTTP {rec.get('status')})")
    return changes


def _load_json(path):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def load_baseline():
    """取"上一份已发布的快照"作为对比基线。

    优先用 PUBLISHED_BASELINE (上次成功推送时的快照); 没有就用仓库里已提交的
    docs/api-snapshot.json (首次运行 / 手动删过基线文件时的兜底)。
    """
    for p in (PUBLISHED_BASELINE, SNAPSHOT_PATH):
        d = _load_json(p)
        if isinstance(d, dict) and d.get("probes"):
            return d
    return {}


def save_published_baseline(snap):
    """推送成功后调用: 把当前快照记为"已发布基线"。"""
    try:
        PUBLISHED_BASELINE.write_text(
            json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def changes_fingerprint(changes):
    """变化的指纹, 用来判断"这组变化是否已经写进 CHANGELOG 了"。"""
    return hashlib.sha1(
        "\n".join(sorted(changes)).encode("utf-8")).hexdigest()


def changelog_already_has(changes):
    """这组变化是否已经写进 CHANGELOG (避免重试推送时重复追加)。"""
    fp = changes_fingerprint(changes)
    try:
        if CHANGELOG_KEY.exists() and \
                CHANGELOG_KEY.read_text(encoding="utf-8").strip() == fp:
            return True
    except Exception:
        pass
    return False


def mark_changelog_written(changes):
    try:
        CHANGELOG_KEY.write_text(changes_fingerprint(changes), encoding="utf-8")
    except Exception:
        pass


def write_status_md(snap):
    """生成 docs/API-STATUS.md (每次重新生成, 反映当前真实状态)。"""
    lines = [
        "# API 实时状态 (自动生成)",
        "",
        f"> 由 `pickcat_probe.py` 于 **{snap.get('generatedAt')}** 自动生成, 不要手改。",
        "> 手写补充请放 README 的「已知 API 坑」一节; 本文件只反映**当前探测到的真实状态**。",
        "",
        f"- 服务可用: **{'是' if snap.get('serviceUp') else '否'}**",
        f"- 探测端点数: {len(snap.get('probes') or {})}",
        "",
        "| 端点 | 说明 | 鉴权 | 状态 | 耗时 | 条目 | 备注 |",
        "|---|---|---|---|---|---|---|",
    ]
    for key, rec in (snap.get("probes") or {}).items():
        auth = "公开" if rec.get("public") else "需登录"
        st = rec.get("status")
        mark = "✅" if st == 200 else ("⚠️" if st == 400 else "❌")
        note = rec.get("code") or ""
        if rec.get("details"):
            note += " " + rec["details"][:80]
        lines.append(
            f"| `{key}` | {rec.get('label')} | {auth} | {mark} {st} | "
            f"{rec.get('latencyMs')}ms | {rec.get('itemCount', '-')} | {note} |"
        )
    lines += [
        "",
        "## 怎么读这张表",
        "",
        "- ✅ 200 = 端点可用",
        "- ⚠️ 400 = **端点存在但参数不合法** (可继续深挖, `details` 里会泄露期望的 schema)",
        "- ❌ 404 = 端点不存在",
        "",
        "### 400 的价值",
        "",
        "服务端用 Zod 校验 query 参数, 且**未知参数直接拒绝**。所以:",
        "",
        "```",
        "GET /api/v1/users/{uid}/posts?role=bogus",
        '-> 400 {\"details\":{\"role\":[\"Invalid input: expected \\\\\"reply\\\\\"\"]}}',
        "```",
        "",
        "这条错误信息等于**接口的 schema 说明书** —— 枚举值、必填参数、类型都在里面。",
        "反过来, 任何 query 参数一旦报 400 且 details 没提到它, 说明**这个参数名不存在**。",
        "",
    ]
    STATUS_MD.write_text("\n".join(lines), encoding="utf-8")


def append_changelog(changes, snap):
    if not changes:
        return False
    # 同一组变化只写一次 (重试推送时别把 CHANGELOG 灌成重复段)
    if changelog_already_has(changes):
        return False
    CHANGELOG_MD.parent.mkdir(parents=True, exist_ok=True)
    if not CHANGELOG_MD.exists():
        CHANGELOG_MD.write_text(
            "# API 变更日志 (自动生成)\n\n"
            "> 由 `pickcat_probe.py` 维护。每次探测发现差异就追加一节。\n\n",
            encoding="utf-8")
    body = [f"\n## {snap.get('generatedAt')}\n"]
    for ch in changes:
        body.append(f"- {ch}")
    with CHANGELOG_MD.open("a", encoding="utf-8") as f:
        f.write("\n".join(body) + "\n")
    mark_changelog_written(changes)
    return True


def _env_token():
    """从 .env 里读 GITHUB_TOKEN (只用于自动推送, 绝不打印)。"""
    f = HERE / ".env"
    if not f.exists():
        return None
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("GITHUB_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _git(*args):
    """在仓库目录里跑 git, 拿输出做判断。"""
    return subprocess.run(["git", *args], cwd=str(HERE),
                          capture_output=True, text=True)


def _autopush_enabled():
    """开关写在 .env 里 (PICKCAT_PROBE_AUTOPUSH=1); 进程环境变量可以覆盖。"""
    ap = os.environ.get("PICKCAT_PROBE_AUTOPUSH")
    ef = HERE / ".env"
    if ap is None and ef.exists():
        for line in ef.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("PICKCAT_PROBE_AUTOPUSH="):
                ap = line.split("=", 1)[1].strip().strip('"').strip("'")
    return ap == "1"


def _heartbeat_hours():
    """心跳间隔 (小时): 无语义变化时, 最多隔这么久刷新一次"巡检时间戳"并提交。"""
    v = os.environ.get("PICKCAT_PROBE_HEARTBEAT_HOURS")
    if v is None:
        ef = HERE / ".env"
        if ef.exists():
            for line in ef.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("PICKCAT_PROBE_HEARTBEAT_HOURS="):
                    v = line.split("=", 1)[1].strip().strip('"').strip("'")
    try:
        return float(v)
    except (TypeError, ValueError):
        return 12.0


def _last_docs_commit_ts():
    """docs/api-snapshot.json 最后一次提交的时间戳 (没提交过则 0)。"""
    r = _git("log", "-1", "--format=%ct", "--", "docs/api-snapshot.json")
    try:
        return int((r.stdout or "0").strip().splitlines()[0])
    except (ValueError, IndexError):
        return 0


def heartbeat_due(force=False):
    """是否该做一次"心跳提交" (只刷新巡检时间戳/耗时, 不记 CHANGELOG)。

    作用: 让仓库里的 API-STATUS.md 永远带一个"多久之前巡检过"的时间戳,
    而不是停留在最后一次有语义变化的那一轮 —— 否则文档看起来像很久没更新了。
    """
    if force:
        return True
    hours = _heartbeat_hours()
    if hours <= 0:
        return False
    last = _last_docs_commit_ts()
    if not last:
        return True
    return (time.time() - last) >= hours * 3600


def maybe_autopush(changes, snap, heartbeat=False):
    """探测到变化时, 把文档变更自动提交并推上 GitHub (让"接口清单"永远是最新的)。

    需要 .env 里放 GITHUB_TOKEN, 且 PICKCAT_PROBE_AUTOPUSH=1 才启用。
    任何失败都只是警告, 不影响巡检结果。
    """
    if not changes and not heartbeat:
        return None
    if not _autopush_enabled():
        return None
    token = _env_token()
    if not token:
        print("⚠ 自动推送已启用但 .env 里没有 GITHUB_TOKEN, 跳过推送。")
        return None

    def run(cmd, **kw):
        return subprocess.run(cmd, cwd=str(HERE), capture_output=True, text=True, **kw)

    try:
        run(["git", "add", "docs/api-snapshot.json", "docs/API-STATUS.md",
             "docs/API-CHANGELOG.md"])
        if changes:
            msg = f"docs: API 巡检发现 {len(changes)} 处变化 ({time.strftime('%Y-%m-%d %H:%M')})"
        else:
            msg = f"docs: API 巡检刷新 (无变化, 更新巡检时间 {time.strftime('%Y-%m-%d %H:%M')})"
        r = run(["git", "commit", "-m", msg])
        if r.returncode != 0 and "nothing to commit" not in (r.stdout + r.stderr):
            print("⚠ git commit 失败:", (r.stderr or r.stdout)[:200])
            return None
        url = "https://xingguangcpp:" + token + "@github.com/xingguangcpp/pickcat-tools.git"
        r2 = run(["git", "-c", "http.sslVerify=false", "push", url, "HEAD:main"])
        if r2.returncode == 0:
            print("✅ 已自动提交并推送到 GitHub")
            # 只有真的推上去了, 才把这轮快照记为"已发布基线"。
            # 推送失败时基线不动 -> 下一轮仍拿旧基线对比 -> 这组变化会被重新
            # 检测到并重试推送, 不会被静默吞掉。
            save_published_baseline(snap)
            return msg
        print("⚠ 自动推送失败:", ((r2.stderr or r2.stdout)[:200]).replace(token, "***"))
        return None
    except Exception as e:
        print("⚠ 自动推送异常:", str(e)[:160])
        return None


def main():
    ap = argparse.ArgumentParser(description="Pickcat API 巡检器 (只读探测 + 自动记录变化)")
    ap.add_argument("--json", action="store_true", help="额外打印机器可读结果")
    ap.add_argument("--always", action="store_true", help="无变化也记一条")
    ap.add_argument("--list", action="store_true", help="只打印探测清单")
    args = ap.parse_args()

    if args.list:
        for label, path, public in PROBES:
            print(f"  {'公开' if public else '需登录':<6} {path:<52} {label}")
        return

    down, ost = service_is_down()
    if down:
        print(f"⛔ 服务不可用 (连续失败 {ost.get('consecutive_failures')} 次), 跳过本轮探测。")
        print(f"   最后失败: {ost.get('last_fail_at')}  冷却期结束后自动再试。")
        return

    c = build_client()
    ctx = build_context(c)
    probes = {}
    print(f"探测 {len(PROBES)} 个端点 (只读)…")
    for label, path, public in PROBES:
        real = _subst(path, ctx)
        rec = run_probe(c, label, real, public)
        probes[f"GET {path}"] = rec
        flag = "OK " if rec.get("status") == 200 else str(rec.get("status"))
        print(f"  {flag:>4} {rec.get('latencyMs', 0):>5}ms  {real}")
        time.sleep(0.3)

    snap = {
        "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "serviceUp": True,
        "probes": probes,
    }
    old = load_baseline()
    changes = diff_snapshots(old, snap)

    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(json.dumps(snap, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    write_status_md(snap)
    appended = append_changelog(changes, snap)

    print("-" * 56)
    if changes:
        print(f"发现 {len(changes)} 处变化:")
        for ch in changes:
            print("  - " + ch)
    else:
        print("与上次快照相比无变化。")
    if appended:
        print(f"已追加 -> {CHANGELOG_MD.name}")
    print(f"已更新 -> {SNAPSHOT_PATH.name}, {STATUS_MD.name}")

    # 心跳: 即使没有语义变化, 也定期把"巡检时间"提交上去, 让仓库里的状态表
    # 始终带着一个新鲜的时间戳 (否则它看起来像很久没巡检过)。
    beat = heartbeat_due(force=args.always)
    if not changes and beat:
        print(f"无变化, 但已到心跳间隔 ({_heartbeat_hours():g}h), 刷新巡检时间戳并提交。")
    maybe_autopush(changes, snap, heartbeat=beat)
    if args.json:
        print(json.dumps({"changes": changes, "snapshot": snap},
                         ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
