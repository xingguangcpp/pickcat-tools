# 多个账号档案 (multi-account)

一个号一份 cookie、一个号一份状态账本 —— 用来把"AI 代发的内容"和"你自己写的内容"
彻底分开放。典型用法：主号（default）你自己写，AI 号（aibot）专门跑自动化。

## 用法

```
accounts/
  aibot.env        <- 某个账号的凭证 (PICKCAT_COOKIE=...), 已被 .gitignore 忽略
  default.env      <- 可选; 不放的话默认读工具根目录的 .env
```

命令行加 `--account <名字>` 即可切换：

```bash
python pickcat_bot.py --account aibot check
python pickcat_cli.py --account aibot post --title "标题" --body "正文"
```

或用环境变量 `PICKCAT_PROFILE=aibot`。

## 状态是隔离的

不同档案的账本 / 配额 / 序号 / 关注列表各存各的, 互不干扰:

| 档案 | 状态文件位置 |
|---|---|
| `default` | 工具根目录 `.wm_ledger.json` / `.wm_quota.json` / `.wm_counter.json` / `.bot_state.json` |
| 其他 (`aibot` 等) | `.state/aibot.wm_ledger.json` / `.state/aibot.wm_quota.json` / ... |

所以两个号的**水印序号各自从 1 开始**, 账本互不混淆 —— 审计时也能一眼看出某条内容是哪个号发的。

## 新账号怎么建

平台没有开放的注册接口(只有 `POST /api/v1/session` 登录), 而且登录有人机验证,
所以注册得你本人在浏览器里做:

1. 浏览器打开 pickcat 社区, 用新邮箱注册并登录 (人机验证你自己过)
2. F12 -> Application -> Cookies -> `https://pickcat.dao3.fun` -> 复制 `pickcat_session` 的值
3. 新建 `accounts/aibot.env`, 写:

```
PICKCAT_COOKIE=pickcat_session=你复制的那串
```

4. 验证: `python pickcat_bot.py --account aibot whoami` (应打印出新号的用户名)

## 提醒

- 每个号的**发帖额度是独立的**(实测 `TOPIC_CREATE` 10 个 / 24h 滚动窗), 评论额度另算。
  新号不等于无限额度, 只是重新从 0 开始。
- 一人多号大概率违反平台规则, 自己心里有数; 这套工具只适合"管理自己账号的内容"。
- Cookie 等同账号凭证, `accounts/*.env` 已被 gitignore, 别手动提交。
