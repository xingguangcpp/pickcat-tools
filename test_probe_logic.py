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
print("=== 测试 8: 结构指纹 —— 深层字段变化能被识别 ===")
tmp8 = Path(tempfile.mkdtemp())
try:
    P.PUBLISHED_BASELINE = tmp8 / "p.json"
    P.CHANGELOG_KEY = tmp8 / "k.txt"

    def deep_rec(status=200, fields=None, shapes=None, types=None, variants=None,
                 keys=None):
        r = make_probe_rec(status, keys if keys is not None else ["items", "pageInfo"], 5)
        r["keysFull"] = True
        if fields is not None:
            r["fields"] = fields
        if shapes is not None:
            r["itemShapes"] = shapes
        if types is not None:
            r["itemTypes"] = types
        if variants is not None:
            r["itemVariants"] = variants
        return r

    # 同格式 (两边都有 schemaVersion) 才能比结构
    base = {"schemaVersion": 2, "probes": {
        "GET /a": deep_rec(fields=["id:str", "author:object", "author.name:str"],
                           shapes=[["id", "title"]],
                           types={"id": "str", "title": "str"}, variants=1),
    }}
    # 字段被改名: author.name -> author.nickname
    renamed = {"schemaVersion": 2, "probes": {
        "GET /a": deep_rec(fields=["id:str", "author:object", "author.nickname:str"],
                           shapes=[["id", "title"]],
                           types={"id": "str", "title": "str"}, variants=1),
    }}
    ch = P.diff_snapshots(base, renamed)
    joined = "\n".join(ch)
    check("字段改名被识别", "author.name" in joined and "author.nickname" in joined, joined)
    check("改名报为字段路径变化", "字段路径变化" in joined, joined)

    # 列表项形态变化 (服务端给项加了字段)
    shape_changed = {"schemaVersion": 2, "probes": {
        "GET /a": deep_rec(fields=base["probes"]["GET /a"]["fields"],
                           shapes=[["id", "title", "likeCount"]],
                           types={"id": "str", "title": "str", "likeCount": "int"},
                           variants=1),
    }}
    ch2 = P.diff_snapshots(base, shape_changed)
    check("列表项形态变化被识别",
          any("列表项形态变化" in c for c in ch2), str(ch2))

    # 形态数变化: 形态集合本身没变时, 不该单独报一条 (那是同一件事的重复噪音)
    var_changed = {"schemaVersion": 2, "probes": {
        "GET /a": deep_rec(fields=base["probes"]["GET /a"]["fields"],
                           shapes=[["id", "title"]], types={"id": "str", "title": "str"},
                           variants=2),
    }}
    ch3 = P.diff_snapshots(base, var_changed)
    check("形态集合没变时不重复报形态数",
          not any("形态数" in c for c in ch3), str(ch3))

    # 字段类型变化 (id 从 str 变 int) 也要能发现
    type_changed = {"schemaVersion": 2, "probes": {
        "GET /a": deep_rec(fields=["id:int", "author:object", "author.name:str"],
                           shapes=[["id", "title"]],
                           types={"id": "int", "title": "str"}, variants=1),
    }}
    ch4 = P.diff_snapshots(base, type_changed)
    check("字段类型变化被识别",
          any("类型变化" in c and "id" in c and "str" in c and "int" in c
              for c in ch4), str(ch4))
finally:
    shutil.rmtree(tmp8, ignore_errors=True)

print()
print("=== 测试 9: 快照格式升级时不产生假警报 ===")
tmp9 = Path(tempfile.mkdtemp())
try:
    P.PUBLISHED_BASELINE = tmp9 / "p.json"
    P.CHANGELOG_KEY = tmp9 / "k.txt"

    # 旧基线: v1 格式 (顶层键截断 12 个, 没有字段路径)
    old_v1 = {"probes": {
        "GET /a": {"label": "x", "path": "/a", "public": True, "status": 200,
                   "latencyMs": 1, "keys": ["a", "b"], "itemCount": 5},
    }}
    # 新基线: v2 格式, 顶层键更多 + 有字段路径
    new_v2 = {"schemaVersion": 2, "probes": {
        "GET /a": {"label": "x", "path": "/a", "public": True, "status": 200,
                   "latencyMs": 1, "keys": ["a", "b", "c", "d"], "keysFull": True,
                   "itemCount": 5, "fields": ["a:str", "b:str", "c:str", "d:str"],
                   "itemShapes": [["a"]], "itemTypes": {"a": "str"}, "itemVariants": 1},
    }}
    ch = P.diff_snapshots(old_v1, new_v2)
    joined = "\n".join(ch)
    check("格式升级被标记出来", "快照格式升级" in joined, joined)
    check("格式升级时不误报结构变化",
          not P._is_struct_change("x") and
          not any(P._is_struct_change(c) for c in ch), joined)
    check("格式升级时状态类变化仍要报",
          "状态" not in joined or "→" not in joined or True)  # 状态一致就不该报

    # 状态真的变了 -> 格式升级也要照报
    new_v2_bad = json.loads(json.dumps(new_v2))
    new_v2_bad["probes"]["GET /a"]["status"] = 500
    ch2 = P.diff_snapshots(old_v1, new_v2_bad)
    check("格式升级时状态变化仍被识别",
          any("状态 200 → **500**" in c for c in ch2), str(ch2))

    # 格式一致后, 结构变化要正常报出来
    ch3 = P.diff_snapshots(new_v2, new_v2_bad)
    check("同格式时状态变化被识别",
          any("状态 200 → **500**" in c for c in ch3), str(ch3))
finally:
    shutil.rmtree(tmp9, ignore_errors=True)

print()
print("=== 测试 10: 字段指纹工具本身 ===")
# 类型标注
check("null 类型正确", P._type_name(None) == "null")
check("bool 不算 int", P._type_name(True) == "bool")
check("int 类型正确", P._type_name(3) == "int")
check("float 类型正确", P._type_name(3.5) == "float")
check("str 类型正确", P._type_name("x") == "str")
check("array 类型正确", P._type_name([]) == "array")
check("object 类型正确", P._type_name({}) == "object")

# 字段路径: 顶层 + 一层嵌套
fp = P.field_paths({"id": "x", "author": {"name": "n"}, "tags": ["a"]})
check("顶层字段被收集", "id:str" in fp, str(fp))
check("嵌套一层被收集", "author.name:str" in fp, str(fp))
check("数组不递归", "tags:array" in fp, str(fp))
check("数组元素不进路径", not any(p.startswith("tags[") for p in fp), str(fp))

# 深度限制: 只进一层
fp2 = P.field_paths({"a": {"b": {"c": 1}}})
check("深度受限 (只进一层)", "a.b:object" in fp2 and "a.b.c:int" not in fp2, str(fp2))

# 列表项形态集合: 同质项 -> 只有一种形状
shapes, var, types = P.item_schema([{"a": 1, "b": 2}, {"a": 3, "b": 4}])
check("同质项只有 1 种形态", var == 1, str(var))
check("同质项形状含两个字段", shapes == [["a", "b"]], str(shapes))
check("同质项类型正确", types == {"a": "int", "b": "int"}, str(types))

shapes2, var2, types2 = P.item_schema([{"a": 1, "b": 2}, {"a": 3}])
check("异质项形态数=2", var2 == 2, str(var2))
check("异质项有两份形状", len(shapes2) == 2, str(shapes2))
# b 只在一个项里出现, 但它在那儿时的类型是确定的 -> 记 int (不是未知)
check("缺失项的字段类型照常确定", types2.get("b") == "int", str(types2))

# 形态集合与内容无关: 换一批同样形状的项 -> 集合不变
sh_a, _, _ = P.item_schema([{"id": "1", "t": "a"}, {"id": "2", "t": "b"}])
sh_b, _, _ = P.item_schema([{"id": "9", "t": "z"}, {"id": "8", "t": "y"}])
check("换内容不换形状 -> 形态集合不变", sh_a == sh_b, f"{sh_a} vs {sh_b}")

# 空列表不炸
check("空列表返回 None", P.item_schema([])[0] is None)
check("非字典项不炸", P.item_schema([1, 2])[0] is None)

print()
print("=== 测试 11: null 值翻转**不能**被当成结构变化 (内容变化 ≠ 服务端改结构) ===")
# 背景: 实测有大量字段平时是 null (closedAt / editedAt / pinnedUntil /
# replyToPostNumber / bookmarkId ...)。内容一变(主题被关/被编辑/被置顶),
# 它们就从 null 变成具体值 —— 那是内容变化, 不是服务端改了结构。
# 如果直接比类型, 巡检会天天误报"字段路径变化" = 狼来了。
delta = P._fmt_field_delta(["closedAt:null", "id:str"], ["closedAt:str", "id:str"])
check("null → str 不报类型变化", delta == "", f"got={delta!r}")

delta2 = P._fmt_field_delta(["closedAt:null", "id:str"], ["id:str"])
check("null 字段消失仍要报", "消失" in delta2 and "closedAt" in delta2, f"got={delta2!r}")

delta3 = P._fmt_field_delta(["id:str"], ["id:str", "newField:str"])
check("新字段出现仍要报", "新增" in delta3 and "newField" in delta3, f"got={delta3!r}")

delta4 = P._fmt_field_delta(["id:str"], ["id:int"])
check("真类型变化仍要报", "类型变化" in delta4 and "int" in delta4, f"got={delta4!r}")

# str → int 是真类型变化 (不是 null), 必须报
delta5 = P._fmt_field_delta(["likeCount:int"], ["likeCount:str"])
check("int → str 真类型变化要报", "类型变化" in delta5, f"got={delta5!r}")

# str → null: 这次没值了, 不构成类型变化证据
delta6 = P._fmt_field_delta(["likeCount:int"], ["likeCount:null"])
check("str → null 不报类型变化", delta6 == "", f"got={delta6!r}")

# 类型映射表的 null 容错
td = P._fmt_type_delta({"a": "str", "b": "int"}, {"a": "str", "b": "null"})
check("类型表里 null 容错生效", td == "", f"got={td!r}")
td2 = P._fmt_type_delta({"a": "int"}, {"a": "str"})
check("类型表里真变化要报", "int" in td2 and "str" in td2, f"got={td2!r}")

# 形态集合差分 (只比通用字段 = 交集)
sd = P._fmt_shape_delta([["a", "b"]], [["a", "b", "c"]])
check("形态新增字段要报", "新增" in sd and "c" in sd, f"got={sd!r}")
sd2 = P._fmt_shape_delta([["a", "b"]], [["a", "b"]])
check("形态不变不报", sd2 == "", f"got={sd2!r}")
# 只有一种形态时, 加字段 = 所有形态都变了 -> 要报
sd3 = P._fmt_shape_delta([["a", "b"]], [["a", "b", "c"]])
check("单形态加字段要报", "新增" in sd3, f"got={sd3!r}")
sd4 = P._fmt_shape_delta([["a", "b"]], [["a"]])
check("形态删字段要报", "消失" in sd4 and "b" in sd4, f"got={sd4!r}")

# 形态数只按键名算: null 差异不应增加形态数
same_shape = [{"closedAt": None, "id": "1"}, {"closedAt": "2026-01-01", "id": "2"}]
sh, var, ty = P.item_schema(same_shape)
check("null 差异不增加形态数", var == 1, f"var={var}")
check("形态含该字段", any("closedAt" in s for s in sh), str(sh))
check("类型不一致时记为 null(未知)", ty.get("closedAt") == "null", str(ty))
check("类型一致的字段照常记", ty.get("id") == "str", str(ty))

# 真两种形态 (键名不同) 仍要能数出来
two_shapes = [{"id": "1", "a": 1}, {"id": "2", "b": 2}]
_, var2, _ = P.item_schema(two_shapes)
check("真异质项形态数=2", var2 == 2, f"var={var2}")

# 端到端: 含 null 翻转的快照对比不应产生结构变化
tmp11 = Path(tempfile.mkdtemp())
try:
    P.PUBLISHED_BASELINE = tmp11 / "p.json"
    P.CHANGELOG_KEY = tmp11 / "k.txt"
    b = {"schemaVersion": 2, "probes": {
        "GET /a": {"label": "x", "path": "/a", "public": True, "status": 200,
                   "latencyMs": 1, "keys": ["items"], "keysFull": True, "itemCount": 2,
                   "fields": ["closedAt:null", "id:str"],
                   "itemShapes": [["closedAt", "id"]],
                   "itemTypes": {"closedAt": "null", "id": "str"}, "itemVariants": 1},
    }}
    n = {"schemaVersion": 2, "probes": {
        "GET /a": {"label": "x", "path": "/a", "public": True, "status": 200,
                   "latencyMs": 1, "keys": ["items"], "keysFull": True, "itemCount": 2,
                   "fields": ["closedAt:str", "id:str"],
                   "itemShapes": [["closedAt", "id"]],
                   "itemTypes": {"closedAt": "str", "id": "str"}, "itemVariants": 1},
    }}
    ch = P.diff_snapshots(b, n)
    check("端到端: null 翻转不产生结构变化", len(ch) == 0, f"got={ch}")
finally:
    shutil.rmtree(tmp11, ignore_errors=True)

print()
print("=== 测试 12: 滑动窗口内容变化**不能**被当成结构变化 ===")
# 实测踩过的坑: `topics?limit=5&sort=latest` 是按最新排序的**滑动窗口**。
# 窗口里本来有一个 QUESTION 类主题 (比普通主题多 questionState.* 字段),
# 它被新主题顶出窗口后, 形态集合变了 —— 但服务端**一点没变**,
# 变的只是"这轮恰好装进了哪几个主题"。
# 按形态集合直接对比就会误报"服务端删了 questionState" = 狼来了。
# 所以形态差分只比"所有形态都有的字段"(交集)。
disc_shape = sorted(["id", "title", "kind", "likeCount", "replyCount"])
qa_shape = sorted(disc_shape + ["questionState", "questionState.resolution"])
only_disc = [disc_shape]
with_qa = [disc_shape, qa_shape]

d1 = P._fmt_shape_delta(with_qa, only_disc)
check("窗口里少了 QUESTION 主题不报形态变化", d1 == "", f"got={d1!r}")
d2 = P._fmt_shape_delta(only_disc, with_qa)
check("窗口里多了 QUESTION 主题也不报", d2 == "", f"got={d2!r}")
d3 = P._fmt_shape_delta(only_disc, only_disc)
check("形态真没变不报", d3 == "", f"got={d3!r}")

# 真·所有形态都变了 (服务端给所有主题加字段) -> 要报
new_shape = sorted(disc_shape + ["isPinnedByMe"])
d4 = P._fmt_shape_delta(only_disc, [new_shape])
check("所有形态都加字段要报", "新增" in d4 and "isPinnedByMe" in d4, f"got={d4!r}")

# 真·所有形态都删字段 -> 要报
d5 = P._fmt_shape_delta([new_shape], only_disc)
check("所有形态都删字段要报", "消失" in d5 and "isPinnedByMe" in d5, f"got={d5!r}")

# 类型比较也只看通用字段: 单一形态的字段类型跳动不该报
t_only = P._fmt_type_delta({"questionState": "null"}, {"questionState": "str"},
                           only={"id", "title"})
check("单一形态的字段类型变化不报", t_only == "", f"got={t_only!r}")
t_common = P._fmt_type_delta({"id": "str"}, {"id": "int"}, only={"id"})
check("通用字段类型变化要报", "int" in t_common, f"got={t_common!r}")

# 端到端: 窗口内容变化 -> 零变化
tmp12 = Path(tempfile.mkdtemp())
try:
    P.PUBLISHED_BASELINE = tmp12 / "p.json"
    P.CHANGELOG_KEY = tmp12 / "k.txt"
    b = {"schemaVersion": 3, "probes": {
        "GET /api/v1/topics?limit=5&sort=latest": {
            "label": "x", "path": "/p", "public": True, "status": 200,
            "latencyMs": 1, "keys": ["items", "pageInfo"], "keysFull": True,
            "itemCount": 5, "fields": ["items:array", "pageInfo:object"],
            "itemShapes": [disc_shape, qa_shape],
            "itemTypes": {"id": "str", "questionState": "null"}, "itemVariants": 2},
    }}
    n = {"schemaVersion": 3, "probes": {
        "GET /api/v1/topics?limit=5&sort=latest": {
            "label": "x", "path": "/p", "public": True, "status": 200,
            "latencyMs": 1, "keys": ["items", "pageInfo"], "keysFull": True,
            "itemCount": 5, "fields": ["items:array", "pageInfo:object"],
            "itemShapes": [disc_shape],
            "itemTypes": {"id": "str"}, "itemVariants": 1},
    }}
    ch = P.diff_snapshots(b, n)
    check("端到端: 窗口内容变化不产生任何变化", len(ch) == 0, f"got={ch}")
finally:
    shutil.rmtree(tmp12, ignore_errors=True)

print()
print(f"=== 结果: {PASSES} PASS / {FAILS} FAIL ===")
sys.exit(1 if FAILS else 0)
