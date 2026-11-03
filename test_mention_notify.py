#!/usr/bin/env python3
"""验证 notify 对 USER_MENTIONED (@我) 的处理 (纯本地, 不发任何网络请求)。

背景: 2026-11-03 发现 —— cmd_notify 只认 REPLY_CREATED 类型的通知,
于是"别人 @ 我提问"这种形态会被整段漏掉, notify 会报"没人回我"。
但社区里又有一种常见玩法: 发个"挑战艾特所有人"的帖, 正文挂一长串 @,
被 @ 到的人都会收到一条 USER_MENTIONED。逐个去回既刷屏又没意义。

所以判据必须是"这条提及是不是真的 @ 了我", 而不是"有没有 USER_MENTIONED"。

要点:
  1. @ 我 -> 要算
  2. @ 全站 (长串用户名里没我) -> 不算
  3. 大小写变体 (@StarLight / @STARLIGHT) -> 算
  4. excerpt 为空 / 缺字段 -> 不算 (宁可漏, 不误报)
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from pickcat_bot import _mention_targets_me  # noqa: E402

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


ME = "星光"

print("=== 测试 1: 真的 @ 了我 -> 要算 ===")
for ex in [
    "@星光 这个 API 怎么调用？",
    "请 @星光 看一下这个帖子",
    "前面 @星光 已经说过了",
]:
    check(f"算作提及: {ex[:26]}", _mention_targets_me({"excerpt": ex}, ME) is True)

print()
print("=== 测试 2: @ 全站的挑战帖 -> 不算 (不能逐个去回) ===")
for ex in [
    "【挑战艾特所有人】全站艾特: @User @user @USer @uwu345 @uzbeb @V_Ri @VesperMare",
    "@someone @everyone @abc @xyz123",
    "",
]:
    check(f"不算作提及: {ex[:26]!r}", _mention_targets_me({"excerpt": ex}, ME) is False)

print()
print("=== 测试 3: 大小写变体 -> 要算 ===")
for me, ex, want in [
    ("StarLight", "@StarLight 在吗", True),
    ("StarLight", "@starlight 在吗", True),
    ("StarLight", "@STARLIGHT 在吗", True),
    ("StarLight", "@starLight 在吗", True),
    ("StarLight", "@星光 在吗", False),
]:
    check(f"me={me} ex={ex[:20]!r} -> {want}",
          _mention_targets_me({"excerpt": ex}, me) is want)

print()
print("=== 测试 4: 缺字段 / 异常输入 -> 不算 (宁可漏不误报) ===")
for details, me in [
    ({}, ME),
    ({"excerpt": None}, ME),
    (None, ME),
    ({"excerpt": "@星光 hi"}, None),
    ({"excerpt": "@星光 hi"}, ""),
    ({"excerpt": "@星光 hi"}, "   "),
]:
    check(f"details={details} me={me!r}",
          _mention_targets_me(details, me) is False)

print()
print(f"=== 结果: {PASSES} PASS / {FAILS} FAIL ===")
sys.exit(1 if FAILS else 0)
