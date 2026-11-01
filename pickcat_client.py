"""
pickcat_client.py — Pickcat 社区(编程猫) 非官方 API 客户端 (Python 标准库版)

重要声明 (务必先读 README.md):
- 这是社区逆向出来的非官方接口, 官方未开放文档, 随时可能失效。
- 使用自动化接口大概率违反平台 ToS。本工具只用于管理"本人账号"内容,
  并内置护栏 (写操作间隔 / dry-run 预览 / 仅限本人 uid)。
- 删除/编辑帖子: 当前已确认的 API 表面里不存在该端点, 本客户端未实现。

已运行时端到端验证 (真实账号 + 真实发帖成功):
  GET  https://pickcat.dao3.fun/api/v1/session              -> 401(无凭证) / 本人信息(有凭证)
  POST https://pickcat.dao3.fun/api/v1/posts               -> 202 异步审核, 后转 PUBLISHED
  GET  https://pickcat.dao3.fun/api/v1/post-submissions/{id} -> 审核状态
  GET  https://pickcat.dao3.fun/api/v1/tags                -> 标签列表
  GET  https://pickcat.dao3.fun/api/v1/users/{uid}/topics  -> 本人帖子列表

⚠️ 主机坑: cdsq.dao3.fun 会把所有请求 302 重定向到 pickcat.dao3.fun。
   用 urllib 时, 302 会把 POST 静默降级成 GET 并丢弃 body, 于是 POST /api/v1/posts
   变成 GET 后返回 404。解决: 默认 base 直接用 pickcat.dao3.fun 直连, 不要走 cdsq。

零依赖: 只用 urllib + http.cookiejar + uuid + json。
"""

import json
import time
import uuid
import urllib.parse
import urllib.request
import urllib.error
from http.cookiejar import CookieJar
from pathlib import Path

DEFAULT_BASE = "https://pickcat.dao3.fun"
USER_AGENT = "pickcat-tools/0.1 (+self-management)"

# 发帖端点: 文档站 / Rust 库均写 /api/v1/posts, 真实服务器也确实认这个路径。
# 之前遇到的 404 不是路径错, 而是 base 用了 cdsq.dao3.fun 触发 302 重定向
# (urllib 把 POST 降级为 GET) 导致的。直连 pickcat.dao3.fun 即正常。
POST_PATH = "/api/v1/posts"
TAGS_PATH = "/api/v1/tags"

# ---------------------------------------------------------------------------
# 隐形水印: 标记"这条内容是走 API 发的", 并给每次写操作编号
#
# 目的有两个:
#   1) 溯源 —— 任何人都可以扒原始文本看出哪些内容是机器人发的 (可验证的标记,
#      而不是靠"感觉像 AI"); 也方便自己区分"浏览器手发的" vs "API 发的"。
#   2) 计数 —— 序号递增, 能统计调用量, 还能看出哪几次调用"丢了"(缺口)。
#
# 字符选型说明:
#   - 主体用 U+2060 (WORD JOINER) 与 U+2061~U+2064 (不可见算子): 它们语义上
#     就是"不可见", 零宽, 不参与任何排版/emoji 组合, 误报概率极低。
#   - U+200D (ZWJ) 也在字母表里 (按需求), 但它对 emoji 有实义, 所以只允许
#     出现在组内、不放在 emoji 序列中间; 实测是否被服务端清洗由 verify 命令验证。
#   - 不选 U+00AD (软连字符, 换行处会显形) / U+FEFF (BOM, 语义特殊)。
#
# 编码: 6 位 base-6 序号 + 1 位校验和(sum(digits) % 6), 定长 7 字符。
# ---------------------------------------------------------------------------
WM_ALPHABET = ["⁠", "⁡", "⁢", "⁣", "⁤", "‍"]
WM_WIDTH = 6
WM_COUNTER_FILE = Path(__file__).resolve().parent / ".wm_counter.json"


def wm_encode(seq):
    """把序号编码成定长隐形水印组 (7 个不可见字符)。"""
    n = int(seq)
    digits = []
    for _ in range(WM_WIDTH):
        digits.append(n % 6)
        n //= 6
    digits.reverse()
    body = "".join(WM_ALPHABET[d] for d in digits)
    return body + WM_ALPHABET[sum(digits) % 6]


def wm_decode(text):
    """从文本里解出所有水印组, 返回 [(seq, start, end), ...]; 无则空列表。"""
    out = []
    i = 0
    n = len(text or "")
    span = WM_WIDTH + 1
    while i + span <= n:
        window = text[i : i + span]
        if all(ch in WM_ALPHABET for ch in window):
            digits = [WM_ALPHABET.index(ch) for ch in window[:WM_WIDTH]]
            if sum(digits) % 6 == WM_ALPHABET.index(window[WM_WIDTH]):
                seq = 0
                for d in digits:
                    seq = seq * 6 + d
                out.append((seq, i, i + span))
                i += span
                continue
        i += 1
    return out


def wm_strip(text):
    """去掉所有水印组, 便于正常显示/对比。"""
    if not text:
        return text
    for _seq, s, e in sorted(wm_decode(text), key=lambda x: -x[1]):
        text = text[:s] + text[e:]
    return text


def wm_apply(text, seq=None, position="end"):
    """给文本加上水印组。position: 'end'(默认, 不干扰首行 markdown) 或 'start'。"""
    if seq is None:
        seq = wm_next_seq()
    group = wm_encode(seq)
    if position == "start":
        return group + (text or "")
    return (text or "") + group


def wm_next_seq():
    """取下一个写操作序号 (持久化, 跨进程递增)。"""
    n = 0
    if WM_COUNTER_FILE.exists():
        try:
            n = int(json.loads(WM_COUNTER_FILE.read_text(encoding="utf-8")).get("seq", 0))
        except Exception:
            n = 0
    n += 1
    try:
        WM_COUNTER_FILE.write_text(
            json.dumps({"seq": n}, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass
    return n



def _parse_ns_cookie_file(path):
    """极简解析 Netscape cookies.txt, 提取 name=value 拼成原始 Cookie 串。

    忽略 # 注释行。每行格式:
    domain<TAB>flag<TAB>path<TAB>secure<TAB>expiry<TAB>name<TAB>value
    之所以自己解析而不是直接用 MozillaCookieJar: 后者在请求时对导入的
    host-only cookie 不触发域名匹配 (已知怪癖), 导致注入失败。这里统一走
    已验证可用的 raw-cookie 注入路径, 更可靠。
    """
    pairs = []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 7:
                pairs.append(f"{parts[5]}={parts[6]}")
            else:
                pairs.append(line)  # 兜底: 当作已是 name=value 片段
    return "; ".join(pairs)


class PickcatError(Exception):
    """API 返回非 2xx 时抛出, 携带解析后的响应体。"""

    def __init__(self, status, body):
        self.status = status
        self.body = body
        try:
            self.code = body.get("code") if isinstance(body, dict) else None
            self.message = body.get("message") if isinstance(body, dict) else None
        except Exception:
            self.code = None
            self.message = None
        super().__init__(f"[{status}] {self.code}: {self.message} | {body}")


class PickcatClient:
    def __init__(
        self,
        username=None,
        password=None,
        base=DEFAULT_BASE,
        min_interval=5.0,
        dry_run=False,
        verbose=False,
        cookie=None,
        cookie_file=None,
    ):
        """
        username/password: 密码登录用 (邮箱+密码)。留空则只做无需鉴权的只读请求。
        cookie: 直接传入浏览器复制的 Cookie 头 (绕过人机验证的合规做法, 见 README)。
        cookie_file: Netscape 格式 cookies.txt 路径 (MozillaCookieJar 加载)。
        base: API 根地址 (不含 /api/v1)。
        min_interval: 两次写操作之间的最小间隔秒数 (护栏, 防止刷太快)。
        dry_run: True 时写操作只打印将要发送的请求, 不真正发出。
        """
        self.base = base.rstrip("/")
        self.username = username
        self.password = password
        self.min_interval = float(min_interval)
        self.dry_run = dry_run
        self.verbose = verbose
        self._raw_cookie = cookie
        if cookie_file:
            self._raw_cookie = _parse_ns_cookie_file(cookie_file)
        self._has_cookie = bool(self._raw_cookie)

        if self._raw_cookie:
            # 原始 Cookie 串: 不走 jar, 每条请求手动注入 (避免被 jar 覆盖)
            self._opener = urllib.request.build_opener()
        else:
            jar = CookieJar()
            self._opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(jar)
            )
        self._last_write = 0.0
        self._session_user_id = None  # 登录后缓存本人 uid

    # ---------- 底层请求 ----------
    def _request(self, method, path, json_body=None, params=None, extra_headers=None):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        data = json.dumps(json_body).encode("utf-8") if json_body is not None else None
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Origin": self.base,  # 源码里 origin 设为 base_url
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        if extra_headers:
            headers.update(extra_headers)
        if self._raw_cookie:
            headers["Cookie"] = self._raw_cookie

        if self.verbose:
            print(f"[req] {method} {url}")
            if data is not None:
                print(f"[req-body] {data.decode('utf-8')[:500]}")

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener.open(req, timeout=30) as r:
                raw = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                body = json.loads(raw) if raw else {}
            except Exception:
                body = {"raw": raw}
            raise PickcatError(e.code, body)
        except Exception as e:  # 网络层错误
            raise PickcatError(-1, {"error": str(e)})

        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return {"raw": raw}

    def _throttle(self):
        """写操作前强制间隔 (护栏)。"""
        now = time.time()
        wait = self.min_interval - (now - self._last_write)
        if wait > 0:
            if self.verbose:
                print(f"[throttle] 等待 {wait:.1f}s 避免过快")
            time.sleep(wait)
        self._last_write = time.time()

    # ---------- 鉴权 ----------
    def login(self):
        """POST /api/v1/session {username, password}。成功后 cookie 自动保存。"""
        if not self.username or not self.password:
            raise PickcatError(-1, {"error": "未提供 username/password"})
        self._request(
            "POST",
            "/api/v1/session",
            json_body={"username": self.username, "password": self.password},
        )
        # 不解析响应, 直接取 session 验证
        return self.get_session()

    def ensure_session(self):
        """获取当前会话: cookie 模式直接取 (无需再登录), 密码模式先登录。"""
        if self._has_cookie:
            return self.get_session()
        return self.login()

    def get_session(self):
        """GET /api/v1/session -> 当前登录用户信息。"""
        ses = self._request("GET", "/api/v1/session")
        if isinstance(ses, dict) and "user" in ses:
            self._session_user_id = ses["user"].get("id")
        return ses

    # ---------- 护栏: 仅限本人 ----------
    def _assert_self(self, user_id):
        if self._session_user_id is None:
            try:
                self.get_session()
            except Exception:
                pass
        if self._session_user_id and user_id != self._session_user_id:
            raise PickcatError(
                -1,
                {
                    "error": "护栏拦截: 目标 uid 与登录账号不一致",
                    "target": user_id,
                    "self": self._session_user_id,
                },
            )

    # ---------- 只读: 列本人帖子 ----------
    def list_user_topics(self, user_id, limit=20):
        """GET /api/v1/users/{uid}/topics (公开, 仅列本人时做护栏校验)。"""
        self._assert_self(user_id)
        return self._request(
            "GET", f"/api/v1/users/{user_id}/topics", params={"limit": limit}
        )

    def list_user_posts(self, user_id, role="reply", limit=20):
        """GET /api/v1/users/{uid}/posts?role=reply (公开)。"""
        self._assert_self(user_id)
        return self._request(
            "GET",
            f"/api/v1/users/{user_id}/posts",
            params={"role": role, "limit": limit},
        )

    def get_topic(self, topic_id):
        return self._request("GET", f"/api/v1/topics/{topic_id}")

    def get_topic_posts(self, topic_id, limit=20, sort=None, cursor=None):
        params = {"limit": limit}
        if sort:
            params["sort"] = sort
        if cursor:
            params["cursor"] = cursor
        return self._request("GET", f"/api/v1/topics/{topic_id}/posts", params=params)

    # ---------- 写: 发帖 (带 Idempotency-Key 护栏) ----------
    def list_tags(self):
        """GET /api/v1/tags -> 可发帖用的标签列表 (每项含 id / name 等)。"""
        return self._request("GET", TAGS_PATH)

    def create_topic(self, title, markdown, kind="DISCUSSION", tag_ids=None,
                     watermark=True, wm_position="end"):
        """
        POST /api/v1/posts 发布主题首楼 (字段名 camelCase, 与文档站一致)。
        需要 Idempotency-Key + Origin 头 (自动加)。返回 202 异步审核。
        服务端校验: tagIds 至少 1 个 (TOO_FEW, min:1), 否则发帖被拒。
        kind 取值: DISCUSSION / QUESTION / ANNOUNCEMENT。
        watermark: 默认在正文末尾追加隐形水印组 (标记"这条是 API 发的" + 写操作序号)。
        """
        tag_ids = tag_ids or []
        if not tag_ids and not self.dry_run:
            raise PickcatError(
                -1,
                {
                    "error": "至少需要 1 个 tagId (服务端限制 min:1)。"
                    "先用 list_tags()/CLI `tags` 命令取真实标签 UUID, 再传入。"
                },
            )
        body = {
            "title": title,
            "markdown": markdown,
            "kind": kind,
            "tagIds": tag_ids,
        }
        if watermark:
            body["markdown"] = wm_apply(body["markdown"], position=wm_position)
        key = str(uuid.uuid4())
        if self.dry_run:
            print(
                f"[dry-run] 将 POST {self.base}/api/v1/posts\n"
                f"  Idempotency-Key: {key}\n"
                f"  body: {json.dumps(body, ensure_ascii=False)[:600]}"
            )
            return {"dry_run": True, "idempotency_key": key, "body": body}
        self._throttle()
        return self._request(
            "POST",
            POST_PATH,
            json_body=body,
            extra_headers={"Idempotency-Key": key},
        )

    def create_reply(self, topic_id, markdown, reply_to_post_number=None,
                     watermark=True, wm_position="end"):
        """POST /api/v1/posts 发布回帖 (可楼中楼)。默认追加隐形水印组。"""
        body = {"topicId": topic_id, "markdown": markdown}
        if reply_to_post_number is not None:
            body["replyToPostNumber"] = reply_to_post_number
        if watermark:
            body["markdown"] = wm_apply(body["markdown"], position=wm_position)
        key = str(uuid.uuid4())
        if self.dry_run:
            print(
                f"[dry-run] 将 POST {self.base}/api/v1/posts (reply to {topic_id})\n"
                f"  Idempotency-Key: {key}\n"
                f"  body: {json.dumps(body, ensure_ascii=False)[:600]}"
            )
            return {"dry_run": True, "idempotency_key": key, "body": body}
        self._throttle()
        return self._request(
            "POST",
            POST_PATH,
            json_body=body,
            extra_headers={"Idempotency-Key": key},
        )

    # ---------- 写后查询: 审核状态 ----------
    def list_post_submissions(self, status=None, topic_id=None, limit=20):
        params = {"limit": limit}
        if status:
            params["status"] = status
        if topic_id:
            params["topicId"] = topic_id
        return self._request("GET", "/api/v1/post-submissions", params=params)

    def get_post_submission(self, submission_id):
        return self._request("GET", f"/api/v1/post-submissions/{submission_id}")
