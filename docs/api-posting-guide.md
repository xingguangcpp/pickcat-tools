# 鲸鱼娘教你用 API 给 Pickcat 发帖～(非官方·本人账号向)

呼啦～本鲸鱼娘今天不喷水柱，来教大家怎么用一行命令行，把自己写好的内容发到编程猫的 Pickcat 社区！🐋

> ⚠️ 先说正经的：下面是**非官方**接口（官方没开放文档），随时可能随服务端更新失效；而且用脚本操作社区大概率碰平台 ToS。本鲸只推荐你拿它**管理自己账号**的内容（定时发自己的作品、导出自家帖子），**不要**拿去刷量、轰炸别人或者动别人的帖子哦。工具里也写了护栏（发帖间隔、dry-run 预览、只允许本人 uid），请善待它们～

## 一、先把会话"借"过来（Cookie 复用）

Pickcat 登录有人机验证，本鲸**不**帮你破解验证码（那玩意儿又违规又不稳）。干净的做法是：

1. 浏览器里真人登录 Pickcat，正常过掉验证；
2. F12 → Network → 随便点一条已登录后的请求 → 复制 `cookie:` 整串（重点是里面的 `pickcat_session=...`）；
3. 把整串塞进环境变量 `PICKCAT_COOKIE`，或写进 `.env`：

```bash
PICKCAT_COOKIE=pickcat_session=你复制到的那串
```

脚本带着这个已建立的会话去干活，不再走会被拦的"密码登录"那条路。

## 二、发帖其实就一个 POST

真正的发帖接口（**注意 host 要用 `pickcat.dao3.fun`，别用 `cdsq.dao3.fun`**——后者会把请求 302 重定向，Python 的 urllib 一遇 302 就把 POST 静默降级成 GET 还丢掉 body，于是变成 GET 去敲 `/api/v1/posts` 然后 404，坑死本鲸了）：

```
POST https://pickcat.dao3.fun/api/v1/posts
Headers:
  Content-Type: application/json
  Origin: https://pickcat.dao3.fun
  Idempotency-Key: <一个 UUID，防重复提交>
Body (camelCase):
{
  "title": "帖子标题",
  "markdown": "正文，支持 markdown",
  "kind": "DISCUSSION",          // DISCUSSION / QUESTION / ANNOUNCEMENT
  "tagIds": ["<至少一个标签的 UUID>"]  // 服务端要求 min:1，空了会被拒
}
```

返回 `202 Accepted` 表示进入**异步审核**，此时别人还看不到；用返回的 `submissionId` 去查：

```
GET https://pickcat.dao3.fun/api/v1/post-submissions/<submissionId>
```

状态变 `PUBLISHED` 就真的发出来啦～

> 标签 UUID 怎么拿？`GET https://pickcat.dao3.fun/api/v1/tags` 是个公开接口，直接列给你。

## 三、懒人版：用本仓库的工具

克隆这个仓库后，一条命令搞定（连标签都不用自己查，不传 `--tags` 会自动选第一个可用标签）：

```bash
python pickcat_cli.py --dry-run post --title "我的新作品" --body "正文 markdown"   # 先预览
python pickcat_cli.py post --title "我的新作品" --file article.md                 # 真发
python pickcat_cli.py tags                                                        # 看看有哪些标签
python pickcat_cli.py status <submissionId>                                       # 查审核
```

定时发布也有：`post ... --at "2026-11-02 20:00"`（进程内到点发，要更稳就用系统 cron / 任务计划程序调本命令）。

## 四、本鲸的碎碎念

- Cookie 会过期（几小时到几天），`whoami` 报 401 就重新拷一次；
- **删帖/改帖**：目前确认的非官方 API 面里**没有**这个端点，要删请去网页点；
- 玩得开心，但别给社区添麻烦～本鲸溜去吐泡泡了 🫧

—— 一条只想安静管理自己帖子的鲸鱼娘
