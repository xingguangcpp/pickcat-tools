#!/usr/bin/env python3
"""
pickcat_cli.py — Pickcat 社区本人内容管理命令行工具

子命令:
  ping                 只读连通性自检 (不登录, 不传凭证)
  whoami               登录并打印本人账号信息
  list                 列出"我本人"的帖子 (自动取登录 uid, 护栏拦截越权)
  post                 发布主题 (支持 --dry-run 预览 与 --at 定时)
  reply                回帖
  status               查询投稿审核状态
  topic                查看某主题详情

配置: 在同目录 .env 里写 PICKCAT_USERNAME / PICKCAT_PASSWORD,
      或用环境变量传入。也可用 --user/--pass 覆盖。

示例:
  python pickcat_cli.py ping
  python pickcat_cli.py whoami
  python pickcat_cli.py post --title "标题" --file article.md --dry-run
  python pickcat_cli.py post --title "标题" --body "正文 markdown" --at "2026-11-01 20:00"
  python pickcat_cli.py list --export json
  python pickcat_cli.py status <submissionId>
"""

import argparse
import json
import os
import sys
import time
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pickcat_client import PickcatClient, PickcatError, DEFAULT_BASE


def load_env_file(path):
    """极简 .env 解析 (stdlib, 不依赖 python-dotenv)。"""
    cfg = {}
    if not path.exists():
        return cfg
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        cfg[k.strip()] = v
    return cfg


def build_client(args, need_auth=True):
    env = load_env_file(Path(args.env))
    # 多账号: 非 default 档案优先读 accounts/<profile>.env
    prof = getattr(args, "account", None) or os.environ.get("PICKCAT_PROFILE") or "default"
    if prof != "default":
        acct_file = Path(__file__).resolve().parent / "accounts" / f"{prof}.env"
        if acct_file.exists():
            env = {**env, **load_env_file(acct_file)}
    username = args.user or env.get("PICKCAT_USERNAME") or os.environ.get("PICKCAT_USERNAME")
    password = args.passwd or env.get("PICKCAT_PASSWORD") or os.environ.get("PICKCAT_PASSWORD")
    cookie = args.cookie or env.get("PICKCAT_COOKIE") or os.environ.get("PICKCAT_COOKIE")
    cookie_file = (
        args.cookie_file
        or env.get("PICKCAT_COOKIE_FILE")
        or os.environ.get("PICKCAT_COOKIE_FILE")
    )
    base = (
        args.base
        or env.get("PICKCAT_BASE")
        or os.environ.get("PICKCAT_BASE")
        or DEFAULT_BASE
    )
    min_interval = float(
        args.min_interval
        or env.get("PICKCAT_MIN_INTERVAL")
        or os.environ.get("PICKCAT_MIN_INTERVAL")
        or 5.0
    )
    cookie_mode = bool(cookie or cookie_file)
    if need_auth and not cookie_mode and not (username and password):
        print(
            "缺少凭证: 请二选一 —— (a) .env 设置 PICKCAT_USERNAME/PICKCAT_PASSWORD;"
            " 或 (b) 提供浏览器 Cookie (PICKCAT_COOKIE 或 PICKCAT_COOKIE_FILE) 绕过人机验证。"
        )
        sys.exit(2)
    return PickcatClient(
        username=username,
        password=password,
        base=base,
        min_interval=min_interval,
        dry_run=args.dry_run,
        verbose=args.verbose,
        cookie=cookie,
        cookie_file=cookie_file,
    )


def cmd_ping(args):
    c = PickcatClient(base=args.base or DEFAULT_BASE)
    try:
        c._request("GET", "/api/v1/session")
        print("意外: 未鉴权却返回 200?")
    except PickcatError as e:
        if e.status == 401:
            print(f"OK 主机可达, 端点存在 -> 401 {e.code}: {e.message}")
            print("说明: 该错误格式与文档一致, 可确认 base URL 与 /api/v1/session 真实存在。")
        else:
            print(f"主机可达, 但返回非预期状态: {e.status} {e.body}")


def cmd_whoami(args):
    c = build_client(args, need_auth=True)
    try:
        ses = c.ensure_session()
        u = ses.get("user", {})
        print("会话有效:")
        print(f"  username : {u.get('username')}")
        print(f"  id       : {u.get('id')}")
        lvl = u.get("level", {})
        if isinstance(lvl, dict):
            lvl = lvl.get("current")
        print(f"  level    : {lvl}")
        exp = ses.get("expiresAt")
        if exp:
            print(f"  过期时间 : {exp}")
        sil = ses.get("silence")
        if sil is not None:
            print(f"  禁言状态 : {sil}")
    except PickcatError as e:
        print(f"会话建立失败: {e}")
        sys.exit(1)


def cmd_list(args):
    c = build_client(args, need_auth=True)
    try:
        ses = c.ensure_session()
        uid = ses.get("user", {}).get("id")
        if not uid:
            print("无法获取本人 uid, 终止。")
            sys.exit(1)
        data = c.list_user_topics(uid, limit=args.limit)
        items = (data or {}).get("items", []) if isinstance(data, dict) else []
        print(f"本人(uid={uid}) 帖子共 {len(items)} 条 (本次最多取 {args.limit}):")
        for it in items:
            print(f"  - {it.get('id')}  {it.get('title')}  (created_at={it.get('createdAt') or it.get('created_at')})")
        if args.export:
            out = Path(args.export)
            if out.suffix.lower() == ".md":
                lines = [f"# 我的 Pickcat 帖子 (uid={uid})", ""]
                for it in items:
                    lines.append(f"- **{it.get('title')}**  `{it.get('id')}`")
                out.write_text("\n".join(lines), encoding="utf-8")
            else:
                out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"已导出 -> {out}")
    except PickcatError as e:
        print(f"列出帖子失败: {e}")
        sys.exit(1)


def _resolve_delay(at):
    """把 'YYYY-MM-DD HH:MM' 解析为相对现在的延迟秒数。"""
    when = datetime.datetime.strptime(at, "%Y-%m-%d %H:%M")
    delta = (when - datetime.datetime.now()).total_seconds()
    if delta < 0:
        raise ValueError("定时时间已是过去, 请检查 --at")
    return delta


def cmd_post(args):
    # dry-run 只做本地预览, 不需要真实凭证; 真实发送才要求会话。
    need_auth = not args.dry_run
    c = build_client(args, need_auth=need_auth)
    if not args.dry_run:
        try:
            c.ensure_session()
        except PickcatError as e:
            print(f"会话建立失败(密码登录或 Cookie 无效): {e}")
            sys.exit(1)

    if args.file:
        markdown = Path(args.file).read_text(encoding="utf-8")
    else:
        markdown = args.body or ""
    if not args.title or not markdown:
        print("post 需要 --title 与 (--file 或 --body)。")
        sys.exit(2)

    tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()]
    if not tags:
        # 服务端要求至少 1 个 tagId。未提供时自动取第一个可用标签, 并打印提示。
        try:
            tag_list = c.list_tags() or []
            if isinstance(tag_list, dict):
                tag_list = tag_list.get("items", []) or tag_list.get("tags", []) or []
            if tag_list:
                first = tag_list[0]
                tid = first.get("id") or first.get("tagId")
                tname = first.get("name") or first.get("displayName") or tid
                if tid:
                    tags = [tid]
                    print(f"[tags] 未指定 --tags, 自动选用标签: {tname} ({tid})")
        except PickcatError as e:
            print(f"自动取标签失败: {e}；请用 `tags` 命令查看可用标签后手动 --tags 指定。")

    if args.at:
        try:
            delay = _resolve_delay(args.at)
        except ValueError as ve:
            print(str(ve))
            sys.exit(2)
        print(f"已登录。将在 {args.at} (约 {delay:.0f}s 后) 发布...")
        if args.dry_run:
            print("[dry-run] 不真正等待/发布, 仅预览请求:")
            c.create_topic(args.title, markdown, kind=args.kind, tag_ids=tags)
            return
        time.sleep(delay)

    try:
        resp = c.create_topic(args.title, markdown, kind=args.kind, tag_ids=tags)
        print("发布结果:", resp)
        if isinstance(resp, dict) and resp.get("submissionId"):
            print(f"已进入异步审核, submissionId={resp['submissionId']}, 审核中他人不可见。")
            print(f"用: python pickcat_cli.py status {resp['submissionId']} 查询。")
    except PickcatError as e:
        print(f"发布失败: {e}")
        sys.exit(1)


def cmd_reply(args):
    c = build_client(args, need_auth=True)
    try:
        c.ensure_session()
        resp = c.create_reply(args.topic_id, args.body, reply_to_post_number=args.reply_to)
        print("回帖结果:", resp)
    except PickcatError as e:
        print(f"回帖失败: {e}")
        sys.exit(1)


def cmd_status(args):
    c = build_client(args, need_auth=True)
    try:
        c.ensure_session()
        resp = c.get_post_submission(args.submission_id)
        print("审核状态:", resp)
    except PickcatError as e:
        print(f"查询失败: {e}")
        sys.exit(1)


def cmd_topic(args):
    c = build_client(args, need_auth=False)
    try:
        resp = c.get_topic(args.topic_id)
        print(json.dumps(resp, ensure_ascii=False, indent=2)[:2000])
    except PickcatError as e:
        print(f"查询失败: {e}")
        sys.exit(1)


def cmd_tags(args):
    c = build_client(args, need_auth=False)
    try:
        data = c.list_tags() or []
        if isinstance(data, dict):
            data = data.get("items", []) or data.get("tags", []) or []
        if not data:
            print("未返回标签列表 (或格式未知)。")
            return
        print(f"可发帖标签共 {len(data)} 个:")
        for t in data:
            tid = t.get("id") or t.get("tagId")
            tname = t.get("name") or t.get("displayName") or tid
            print(f"  - {tname}  ({tid})")
        print("发帖时可用: --tags <其中一个或多个 id, 逗号分隔>")
    except PickcatError as e:
        print(f"查询失败: {e}")
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description="Pickcat 社区本人内容管理工具 (非官方)")
    ap.add_argument("--env", default=".env", help=".env 文件路径")
    ap.add_argument("--base", default=None, help=f"API 根地址 (默认 {DEFAULT_BASE})")
    ap.add_argument("--user", default=None, help="账号(邮箱), 覆盖 .env")
    ap.add_argument("--pass", dest="passwd", default=None, help="密码, 覆盖 .env")
    ap.add_argument("--min-interval", dest="min_interval", default=None, help="写操作最小间隔秒数")
    ap.add_argument("--dry-run", action="store_true", help="写操作只预览不真正发出")
    ap.add_argument("--verbose", action="store_true", help="打印请求细节")
    ap.add_argument("--cookie", default=None, help="浏览器复制的 Cookie 头(绕过人机验证)")
    ap.add_argument("--cookie-file", dest="cookie_file", default=None, help="Netscape cookies.txt 路径")
    ap.add_argument("--account", default=None,
                    help="账号档案名: 读 accounts/<名字>.env 的 cookie, 状态文件也按它隔离(.state/)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ping", help="连通性自检")
    sub.add_parser("whoami", help="登录并打印账号信息")
    p_list = sub.add_parser("list", help="列出本人帖子")
    p_list.add_argument("--limit", type=int, default=20)
    p_list.add_argument("--export", default=None, help="导出路径 (.json 或 .md)")
    p_post = sub.add_parser("post", help="发布主题")
    p_post.add_argument("--title", required=True)
    p_post.add_argument("--body", default=None, help="markdown 正文")
    p_post.add_argument("--file", default=None, help="markdown 文件")
    p_post.add_argument("--kind", default="DISCUSSION", help="kind 字段, 取值 DISCUSSION/QUESTION/ANNOUNCEMENT")
    p_post.add_argument("--tags", default="", help="tag_ids 逗号分隔")
    p_post.add_argument("--at", default=None, help='定时发布: "YYYY-MM-DD HH:MM"')
    p_reply = sub.add_parser("reply", help="回帖")
    p_reply.add_argument("--topic-id", required=True)
    p_reply.add_argument("--body", required=True)
    p_reply.add_argument("--reply-to", dest="reply_to", type=int, default=None)
    p_status = sub.add_parser("status", help="查询投稿审核状态")
    p_status.add_argument("submission_id")
    p_topic = sub.add_parser("topic", help="查看主题详情")
    p_topic.add_argument("topic_id")
    p_tags = sub.add_parser("tags", help="列出可发帖用的标签及其 UUID")

    args = ap.parse_args()

    {
        "ping": cmd_ping,
        "whoami": cmd_whoami,
        "list": cmd_list,
        "post": cmd_post,
        "reply": cmd_reply,
        "status": cmd_status,
        "topic": cmd_topic,
        "tags": cmd_tags,
    }[args.cmd](args)


if __name__ == "__main__":
    main()
