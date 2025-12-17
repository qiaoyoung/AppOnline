## App Store 上架状态监控（无服务器版）

你的需求：定期检测某个 App Store 链接（例如 `https://apps.apple.com/ph/app/id6756509310`）是否还能正常访问/查询，用来第一时间发现应用被下架或某国家/地区不可用。

本方案特点：
- 不需要自建云服务器
- 支持很多 App、每个 App 不同国家/地区
- 定时任务由 GitHub Actions 执行（默认每 30 分钟）
- 告警支持微信：优先企业微信机器人，其次 Server酱（个人微信）

---

## 1. 原理（我们监控的“信号”）

脚本只做一件事：访问你配置的 App Store 链接 `store_url`。

- 如果请求失败（网络异常/超时）或 HTTP 状态码不是 200：认为“链接访问不到”，触发告警
- 如果 HTTP 200：认为正常

---

## 2. 配置要监控的 App（多应用 + 不同国家）

编辑 `monitor/apps.json`，每个元素代表一个 App：

- `name`：用于告警展示
- `app_id`：App Store 的数字 id（来自链接里的 `idxxxx`）
- `store_url`：你希望探测的页面 URL（通常就是你给用户的商店链接）
- `listed_at`：上架时间（你自己填写，用于计算“从上架到下架经历多久”），建议格式 `YYYY-MM-DD`（例如 `2025-12-17`）

示例（当前已内置你的 Mozyor）：
- `name`: Mozyor
- `app_id`: 6756509310
- `store_url`: https://apps.apple.com/ph/app/id6756509310
- `listed_at`: 2025-12-17

---

## 3. 选择微信告警方式（推荐企业微信机器人）

### 方案 A（推荐）：企业微信机器人 Webhook
优点：稳定、无需关注/订阅、到达率高。

你需要：
1) 进入企业微信群 → 添加群机器人 → 复制 Webhook 地址
2) 在 GitHub 仓库里配置 Secrets（见下节）

注意：
- 企业微信机器人消息会发送到「企业微信」App 里的群，不会直接进入你的个人微信聊天列表。
- 如果你只有个人微信、且不想额外装企业微信，那么更适合用 Server酱。

### 方案 B：Server酱（个人微信）
优点：个人微信即可接收。

你需要：
1) 在 Server酱获取 `SENDKEY`
2) 在 GitHub Secrets 里配置 `SERVERCHAN_SENDKEY`

个人微信接入要点（简版）：
- 你需要在 Server酱后台拿到 `SENDKEY`（也就是脚本里用的 `SERVERCHAN_SENDKEY`）。
- 一般还需要在微信里关注/绑定 Server酱对应的服务号/小程序，才能把推送送到你的个人微信。

---

## 4. 配置 GitHub Actions Secrets

进入你的 GitHub 仓库：
`Settings` → `Secrets and variables` → `Actions` → `New repository secret`

按你选择的告警方式配置：

### 用企业微信机器人时
- `NOTIFY_CHANNEL`：填 `wecom`
- `WECOM_WEBHOOK_URL`：填你的企业微信机器人 Webhook

### 用 Server酱时
- `NOTIFY_CHANNEL`：填 `serverchan`
- `SERVERCHAN_SENDKEY`：填你的 SENDKEY

你也可以两个都配置，但 `NOTIFY_CHANNEL` 决定实际走哪一个。

---

## 5. 定时频率与容错

默认每 30 分钟跑一次（`cron: "*/30 * * * *"`）。

脚本内置：
- 重试 `RETRIES=2`（总共最多 3 次尝试）
- 超时 `TIMEOUT_SECONDS=10`
- 失败会让 workflow 直接失败，并发送告警（告警里会包含上架时间与“上架至今耗时”）

如果你希望改频率：
编辑 `.github/workflows/appstore-monitor.yml` 的 `cron` 即可。

---

## 7. 告警策略（当前仓库默认）

- 只要任意 App 探测失败，就会发送告警
- 告警内容只包含「异常的 App」（正常的不会打扰你）
- HTTP 404/410 会被标记为“强信号：可能下架/该区不可用”；其它非 200/网络异常则更可能是临时网络/限流/风控（但仍会告警）

---

## 8. 避免重复告警（只在首次异常时推送一次）

默认已在 GitHub Actions 中开启“仅首次异常告警”模式：
- 只有当某个 App 状态从「正常 → 异常」的那一次，才会发送告警
- 如果某个 App 一直异常，后续定时运行不会重复发送

实现方式：
- 脚本会把上一次各 App 的状态保存到 `monitor/.state/appstore_state.json`
- GitHub Actions 使用 `actions/cache` 把 `monitor/.state/` 跨次运行缓存下来

相关环境变量（workflow 已帮你配置好）：
- `ALERT_MODE=transition`：只在状态切换为异常时告警
- `STATE_FILE=monitor/.state/appstore_state.json`：状态文件路径

---

## 6. 本地运行（可选）

你也可以在本地先跑一遍看输出：

1) 安装依赖
`pip install -r monitor/requirements.txt`

2) 运行
`python monitor/monitor_appstore.py`

如果你要本地也发通知，可以在环境变量里设置：
- 企业微信：`NOTIFY_CHANNEL=wecom` + `WECOM_WEBHOOK_URL=...`
- Server酱：`NOTIFY_CHANNEL=serverchan` + `SERVERCHAN_SENDKEY=...`


