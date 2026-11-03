#!/usr/bin/env python3
"""验证"连发回答合并"逻辑 (纯本地, 不发任何网络请求)。

背景: 2026-11-03 实测 —— 用户 c 把一条回答拆成 #17/#18 两楼发 (同作者、
同 replyToPostNumber=16、相隔 56 秒)。我回了 #18, 内容同时接住了 #17 的观点,
但"我回过哪些楼层"原先只按 replyToPostNumber **精确匹配** —— 于是 #17 仍被判为
"没回过", 下一轮 notify 会把它当新待回复, 白花回复额度, 还可能被平台看成刷屏。

要点:
  1. 同作者 + 同父楼层 + 楼层号紧邻 + 时间接近 => 算同一条连发回答
  2. 父楼层不同 / 作者不同 / 时间隔太久 / 楼层号跨太远 => **不能**并
     (误并 = 真有问题没被看见, 比漏并更糟)
  3. 只回其中一楼的同父楼层, 兄弟楼层不该再冒出来
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pickcat_bot as B  # noqa: E402

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


def P(pn, author="c", rtp=16, at="2026-11-03T00:04:23.868Z"):
    return {"postNumber": pn, "author": {"id": author}, "replyToPostNumber": rtp,
            "createdAt": at}


print("=== 测试 1: c 的 #17/#18 场景 (同父层、相隔 56 秒) ===")
# 服务端楼层: 15=c, 16=我, 17=c(rtp=16), 18=c(rtp=16), 19=我(rtp=18)
items = [
    P(15, "c", 14, "2026-11-01T04:24:32.589Z"),
    P(16, "ME", 15, "2026-11-02T16:53:39.196Z"),
    P(17, "c", 16, "2026-11-03T00:04:23.868Z"),
    P(18, "c", 16, "2026-11-03T00:05:19.017Z"),
    P(19, "ME", 18, "2026-11-03T00:48:44.788Z"),
]
groups = B._burst_siblings(items)
# 我回过 18 -> 同组的 17 也应被算回过
merged = set()
for sibs in groups.values():
    if 18 in sibs:
        merged |= set(sibs)
check("#17/#18 归为同一组", 17 in merged and 18 in merged, f"merged={merged}")
check("#16 不该被并进来 (我自己的楼)", 16 not in merged, f"merged={merged}")
check("#15 不该被并进来 (另一个父层)", 15 not in merged, f"merged={merged}")

print("\n=== 测试 2: 父楼层不同 => 不能并 ===")
items2 = [
    P(17, "c", 16, "2026-11-03T00:04:23.868Z"),
    P(18, "c", 10, "2026-11-03T00:05:19.017Z"),  # 父层不同
]
groups2 = B._burst_siblings(items2)
merged2 = set()
for sibs in groups2.values():
    if 18 in sibs:
        merged2 |= set(sibs)
check("父层不同不合并", 17 not in merged2, f"merged={merged2}")

print("\n=== 测试 3: 作者不同 => 不能并 ===")
items3 = [
    P(17, "c", 16, "2026-11-03T00:04:23.868Z"),
    P(18, "other", 16, "2026-11-03T00:05:19.017Z"),
]
groups3 = B._burst_siblings(items3)
merged3 = set()
for sibs in groups3.values():
    if 18 in sibs:
        merged3 |= set(sibs)
check("作者不同不合并", 17 not in merged3, f"merged={merged3}")

print("\n=== 测试 4: 时间隔太久 => 不能并 ===")
items4 = [
    P(17, "c", 16, "2026-11-03T00:04:23.868Z"),
    P(18, "c", 16, "2026-11-03T02:30:19.017Z"),  # 2.5 小时后
]
groups4 = B._burst_siblings(items4)
merged4 = set()
for sibs in groups4.values():
    if 18 in sibs:
        merged4 |= set(sibs)
check("隔太久不合并", 17 not in merged4, f"merged={merged4}")

print("\n=== 测试 5: 楼层号跨太远 => 不能并 ===")
items5 = [
    P(17, "c", 16, "2026-11-03T00:04:23.868Z"),
    P(21, "c", 16, "2026-11-03T00:05:19.017Z"),  # 中间隔了 3 楼
]
groups5 = B._burst_siblings(items5)
merged5 = set()
for sibs in groups5.values():
    if 21 in sibs:
        merged5 |= set(sibs)
check("楼层号跨太远不合并", 17 not in merged5, f"merged={merged5}")

print("\n=== 测试 5b: 隔一楼的同父楼层 (gap=2) => 不能并 ===")
# 阈值收紧到 gap<=1 后, 中间夹了别的楼层就不该并 —— 误并会把真问题藏起来。
items5b = [
    P(17, "c", 16, "2026-11-03T00:04:23.868Z"),
    P(18, "x", 16, "2026-11-03T00:04:50.000Z"),  # 别人插了一楼
    P(19, "c", 16, "2026-11-03T00:05:19.017Z"),
]
groups5b = B._burst_siblings(items5b)
merged5b = set()
for sibs in groups5b.values():
    if 19 in sibs:
        merged5b |= set(sibs)
check("夹了别人就不合并", 17 not in merged5b, f"merged={merged5b}")

print("\n=== 测试 6: 三楼连发 (同父层) 应归一组 ===")
items6 = [
    P(17, "c", 16, "2026-11-03T00:04:23.868Z"),
    P(18, "c", 16, "2026-11-03T00:05:19.017Z"),
    P(19, "c", 16, "2026-11-03T00:06:10.500Z"),
]
groups6 = B._burst_siblings(items6)
merged6 = set()
for sibs in groups6.values():
    if 19 in sibs:
        merged6 |= set(sibs)
check("三楼连发归一组", merged6 == {17, 18, 19}, f"merged={merged6}")

print("\n=== 测试 7: _post_ts 解析 ===")
check("解析 createdAt", B._post_ts({"createdAt": "2026-11-03T00:04:23.868Z"}) is not None)
check("空 createdAt 返回 None", B._post_ts({"createdAt": None}) is None)
check("乱格式返回 None", B._post_ts({"createdAt": "not-a-date"}) is None)

print(f"\n{'=' * 50}")
print(f"通过 {PASSES} / 失败 {FAILS}")
sys.exit(1 if FAILS else 0)
