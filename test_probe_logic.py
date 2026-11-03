#!/usr/bin/env python3
"""验证巡检器的"推送失败不丢变化"逻辑 (纯本地, 不发任何网络请求)。

场景: 第 1 轮 push 失败 -> 第 2 轮必须**重新检测到**同一组变化并重试。
旧逻辑拿"本地已覆盖的 docs/api-snapshot.json"当基线, 变化会被静默吞掉。
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pickcat_probe as P

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


def make_probe_rec(status=200, keys=None, items=1, code=None, details=None):
    r = {"label": "x", "path": "/x", "public": True, "status": status,
         "latencyMs": 100}
    if keys is not None:
        r["keys"] = keys
    if items is not None:
        r["itemCount"] = items
    if code:
        r["code"] = code
    if details:
        r["details"] = details
    return r


print("=== 测试 1: 推送失败后, 下一轮仍能重新检测到变化 ===")
tmp = Path(tempfile.mkdtemp())
try:
    # 把基线/CHANGELOG 状态文件指到临时目录, 不污染真实仓库
    P.PUBLISHED_BASELINE = tmp / "published.json"
    P.CHANGELOG_KEY = tmp / "key.txt"

    snap_v1 = {"generatedAt": "t1", "serviceUp": True,
               "probes": {"GET /a": make_probe_rec(200, ["items"], 5)}}
    snap_v2 = {"generatedAt": "t2", "serviceUp": True,
               "probes": {"GET /a": make_probe_rec(200, ["items"], 7)}}

    # 第 1 轮: 基线 = v1, 探测到 v2 -> 有变化
    P.save_published_baseline(snap_v1)
    ch1 = P.diff_snapshots(P.load_baseline(), snap_v2)
    check("第1轮检测到条目数变化", len(ch1) == 1, f"got={ch1}")

    # 模拟"推送失败": 不调用 save_published_baseline (基线保持 v1)
    # 本地 docs/api-snapshot.json 被覆盖成了 v2 (旧逻辑的致命点)
    (tmp / "api-snapshot.json").write_text(json.dumps(snap_v2), encoding="utf-8")
    P.SNAPSHOT_PATH = tmp / "api-snapshot.json"

    # 第 2 轮: 旧逻辑会用本地文件当基线 -> 无变化(丢失!); 新逻辑用已发布基线 -> 仍检测到
    ch2 = P.diff_snapshots(P.load_baseline(), snap_v2)
    check("第2轮(推送失败后)仍重新检测到变化", len(ch2) == 1, f"got={ch2}")

    # 第 3 轮: 推送成功后基线更新 -> 不应再报变化
    P.save_published_baseline(snap_v2)
    ch3 = P.diff_snapshots(P.load_baseline(), snap_v2)
    check("第3轮(推送成功后)不再报变化", len(ch3) == 0, f"got={ch3}")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("=== 测试 2: CHANGELOG 不会重复追加同一组变化 ===")
tmp2 = Path(tempfile.mkdtemp())
try:
    P.CHANGELOG_KEY = tmp2 / "key.txt"
    P.CHANGELOG_MD = tmp2 / "CHANGELOG.md"
    changes = ["**新增** `GET /x` — 测试 (HTTP 200)"]
    check("首次追加成功", P.append_changelog(changes, {"generatedAt": "t1"}) is True)
    check("重复追加被跳过", P.append_changelog(changes, {"generatedAt": "t2"}) is False)
    text = P.CHANGELOG_MD.read_text(encoding="utf-8")
    check("CHANGELOG 里只出现一段", text.count("新增") == 1, f"count={text.count('新增')}")
finally:
    shutil.rmtree(tmp2, ignore_errors=True)

print()
print("=== 测试 3: 状态码/错误码/结构/details 变化都能被识别 ===")
tmp3 = Path(tempfile.mkdtemp())
try:
    P.PUBLISHED_BASELINE = tmp3 / "p.json"
    P.CHANGELOG_KEY = tmp3 / "k.txt"
    base = {"probes": {
        "GET /a": make_probe_rec(200, ["items", "pageInfo"], 5, "OK", None),
        "GET /b": make_probe_rec(400, None, None, "VALIDATION_FAILED", '{"role":["bad"]}'),
    }}
    new = {"probes": {
        "GET /a": make_probe_rec(500, ["items", "pageInfo"], 5, "SERVER_ERR", None),
        "GET /b": make_probe_rec(400, None, None, "VALIDATION_FAILED", '{"role":["other"]}'),
        "GET /c": make_probe_rec(200, ["items"], 1),
    }}
    ch = P.diff_snapshots(base, new)
    joined = "\n".join(ch)
    check("状态变化被识别", "状态 200 → **500**" in joined, joined)
    check("错误码变化被识别", "错误码" in joined, joined)
    check("details 变化被识别", "错误详情变化" in joined, joined)
    check("新增端点被识别", "**新增**" in joined, joined)

    # 消失端点 (单独构造: new 里没有 /b)
    ch2 = P.diff_snapshots(base, {"probes": {"GET /a": base["probes"]["GET /a"]}})
    check("消失端点被识别", any("**消失**" in c for c in ch2), str(ch2))
    check("消失端点带原状态码", any("原 HTTP 400" in c for c in ch2), str(ch2))
finally:
    shutil.rmtree(tmp3, ignore_errors=True)

print()
print("=== 测试 4: 心跳判定 ===")
try:
    P.PUBLISHED_BASELINE = Path(tempfile.mkdtemp()) / "p.json"
    # 强制心跳 -> True
    check("force=True 触发心跳", P.heartbeat_due(force=True) is True)
    # 间隔设为 0 -> 关闭心跳
    import os
    os.environ["PICKCAT_PROBE_HEARTBEAT_HOURS"] = "0"
    check("间隔=0 时关闭心跳", P.heartbeat_due() is False)
    os.environ["PICKCAT_PROBE_HEARTBEAT_HOURS"] = "12"
    check("间隔=12h 时走正常判定", isinstance(P.heartbeat_due(), bool))
    del os.environ["PICKCAT_PROBE_HEARTBEAT_HOURS"]
finally:
    shutil.rmtree(P.PUBLISHED_BASELINE.parent, ignore_errors=True)

print()
print("=== 测试 5: 鉴权属性解读 (匿名实测 -> 判定) ===")
check("匿名 200 = 公开", P.interpret_auth(200, None) == "公开")
check("匿名 401 = 需登录", P.interpret_auth(401, None) == "需登录")
check("匿名 403 = 需权限", P.interpret_auth(403, None) == "需权限")
check("匿名 400 = 公开(鉴权先于校验)", P.interpret_auth(400, None) == "公开")
check("匿名 404 = 不存在", P.interpret_auth(404, None) == "不存在")
check("匿名 -1 = 未判定", P.interpret_auth(-1, None) == "未判定")

print()
print("=== 测试 6: 匿名探测计划自动派生, 不漏不重 ===")
plan = P.anon_probe_plan()
routes = [p.split("?", 1)[0] for _l, p in plan]
check("派生计划非空", len(plan) > 0, f"got={len(plan)}")
check("每条路由只出现一次", len(routes) == len(set(routes)),
      f"dup={len(routes) - len(set(routes))}")
# 计划里的路由必须都能在 PROBES 里找到 (保证是从 PROBES 派生的, 不是手写漂移)
probe_routes = {p.split("?", 1)[0] for _l, p, _pub in P.PROBES}
check("计划路由全部来自 PROBES", set(routes) <= probe_routes,
      f"extra={set(routes) - probe_routes}")
check("每个 PROBES 路由都被覆盖", probe_routes <= set(routes),
      f"missed={probe_routes - set(routes)}")

print()
print("=== 测试 7: 鉴权属性变化能被识别 (端点还在, 但谁能看变了) ===")
tmp7 = Path(tempfile.mkdtemp())
try:
    P.PUBLISHED_BASELINE = tmp7 / "p.json"
    P.CHANGELOG_KEY = tmp7 / "k.txt"

    def auth_rec(mode, declared=None, mismatch=False, anon=200):
        return {"label": "x", "anonStatus": anon, "anonCode": None,
                "mode": mode, "declared": declared or mode,
                "mismatch": mismatch}

    base = {"probes": {"GET /a": make_probe_rec(200, ["items"], 5)},
            "auth": {"/api/v1/a": auth_rec("公开")}}
    new = {"probes": {"GET /a": make_probe_rec(200, ["items"], 5)},
           "auth": {"/api/v1/a": auth_rec("需登录", "公开", True, 401)}}
    ch = P.diff_snapshots(base, new)
    joined = "\n".join(ch)
    check("鉴权属性变化被识别", "鉴权属性变化" in joined, joined)
    check("标注与实测矛盾被识别", "标注与实测不符" in joined, joined)

    # 矛盾"翻转"回来 -> 只报一次"现已一致", 之后不再重复报
    back = {"probes": {"GET /a": make_probe_rec(200, ["items"], 5)},
            "auth": {"/api/v1/a": auth_rec("公开", "公开", False, 200)}}
    ch2 = P.diff_snapshots(new, back)
    check("矛盾翻转被识别", any("现已一致" in c for c in ch2), str(ch2))
    ch3 = P.diff_snapshots(back, back)
    check("状态稳定时不重复报矛盾", not any("标注与实测" in c for c in ch3), str(ch3))

    # 路由消失
    ch4 = P.diff_snapshots(base, {"probes": base["probes"], "auth": {}})
    check("路由消失被识别", any("**消失(路由)**" in c for c in ch4), str(ch4))

    # 缺口回归: **新路由**上的标注矛盾也必须报出来。
    # (旧实现里 prev is None 就 continue, 新加探测项把公开端点标错根本不会报警)
    new_bad = {"probes": {"GET /b": make_probe_rec(200, ["items"], 5)},
               "auth": {"/api/v1/b": auth_rec("需登录", "公开", True, 401)}}
    ch5 = P.diff_snapshots({"probes": {}, "auth": {}}, new_bad)
    check("新路由的标注矛盾被识别",
          any("标注与实测不符" in c for c in ch5), str(ch5))
finally:
    shutil.rmtree(tmp7, ignore_errors=True)

print()
print(f"=== 结果: {PASSES} PASS / {FAILS} FAIL ===")
sys.exit(1 if FAILS else 0)
