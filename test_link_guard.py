#!/usr/bin/env python3
"""验证"正文含站外链接"护栏 (纯本地, 不发任何网络请求)。

背景: 2026-11-03 实测 —— 服务端对 markdown 里的站外链接直接回
`422 EXTERNAL_LINK_NOT_ALLOWED (field=markdown, host=timeapi.io)`,
整条写操作失败, 并且**白烧一个水印序号**(账本 seq=46 留缺口)。
所以在发请求之前本地自检, 把失败变成"不浪费序号的明确报错"。

要点:
  1. 真域名要能被抓到 (bare domain / 显式 URL / www.)
  2. **不能误伤带点的 API 名** (entity.velocity / getGroupStorage / player.userKey)
     —— 这是本护栏最容易写坏的地方, 之前三条成功回复正文里全有这类标识符
  3. 版本号 / 文件名 / 邮箱 / 社区自己的域名 都不能误报
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from pickcat_client import find_external_links, PickcatClient  # noqa: E402

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


print("=== 测试 1: 真域名必须被抓到 ===")
for text, want in [
    ("timeapi.io 是通的", "timeapi.io"),
    ("参考 https://example.com/foo 这个文档", "example.com"),
    ("见 www.example.com 就行", "example.com"),
    ("用 gitee.com 拉类型定义", "gitee.com"),
    ("https://github.com/x/y 那个仓库", "github.com"),
    ("多个都要抓: a.com 和 b.org", "a.com"),
]:
    hosts = find_external_links(text)
    check(f"抓到 {want}", want in hosts, f"got={hosts}")

print()
print("=== 测试 2: 不能误伤带点的 API 名 (最容易写坏的地方) ===")
for text in [
    "storage.getGroupStorage 读回来的记录自带 updateTime",
    "配合 entity.player.userKey 当玩家标识",
    "移动走 entity.velocity 比较稳",
    "用 world.onPlayerLeave 判定退出",
    "increment 是原子自增, createTime 也在",
    "Kitten-Cloud-Function, CoCo 控件版",
    "kittenapi 那个项目就是把它包成 REST 接口",
    "Pygbag 打包成 build/web 静态文件",
    "lightMode 设 manual, skyEnabled 关掉",
]:
    hosts = find_external_links(text)
    check(f"不误报: {text[:34]}", hosts == [], f"got={hosts}")

print()
print("=== 测试 3: 版本号/文件名/邮箱/社区域名 不能误报 ===")
for text in [
    "版本 4.0.1 和 v1.2.3 都试过",
    "文件 abc.def 和 xyz.gh",
    "发到 foo@bar.com 就行",
    "见 https://pickcat.dao3.fun/api/v1/session",
]:
    hosts = find_external_links(text)
    check(f"不误报: {text[:34]}", hosts == [], f"got={hosts}")

print()
print("=== 测试 4: 护栏在消耗水印序号之前就拦下 ===")
try:
    PickcatClient._assert_no_external_link("去看 timeapi.io 这个接口")
    check("外链被拦下", False, "居然没抛异常")
except Exception as e:
    body = getattr(e, "body", {}) or {}
    check("外链被拦下", True)
    check("报错带 hosts 便于改写", "timeapi.io" in str(body.get("hosts", "")), str(body))
    check("报错带改写提示", "文字" in str(body.get("hint", "")), str(body))

# 正常正文必须放行
try:
    PickcatClient._assert_no_external_link("用 entity.velocity 比较稳, 云变量只是缓存")
    check("正常正文放行", True)
except Exception as e:
    check("正常正文放行", False, f"误拦了: {e}")

print()
print(f"=== 结果: {PASSES} PASS / {FAILS} FAIL ===")
sys.exit(1 if FAILS else 0)
