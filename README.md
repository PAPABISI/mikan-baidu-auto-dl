# mikan-baidu-auto-dl

> 蜜柑追番 + 百度网盘自动化：每周检查追番列表新集，调用百度网盘离线下载接口入盘。配套 Web 控制台用于增删追番、看进度、手动触发。

## 为什么造这个轮子

GitHub 上现有追番工具（AutoBangumi、Alist-MikananiRss 等）几乎都走 qBittorrent/aria2 + 本地 NAS 的架构。但如果你只想把番塞进**百度网盘**离线下载——不需要 NAS、不需要 BT 客户端、就是想用网盘会员那点配额——这条路没人铺。

本仓库就是铺这条路：**直接调百度网盘 web API 的 `cloud_dl.add_task`**，不依赖任何外部下载器。

## 特性

- **配置驱动**：所有追番项写在 `追番列表.json`，改文件即调整行为
- **零外部依赖**：标准库实现，PyPI 不可达也能跑（实测 PyPI 走默认沙箱代理全 502）
- **登录态策略 B**：扫码一次拿到 `BDUSS` 后缓存到 `.baidu_cookies.json`，跑前自检，失效才需再扫
- **增量抓取**：每次只处理比 `last_episode` 更新的话
- **状态分桶**：`QRCODE_NEEDED` / `NET_ERROR` / `NO_UPDATES` / `OK` / `GENERIC`，便于上层 automation 做差异化通知
- **蜜柑走 Clash 代理 + 百度走直连**：两套网络通道，互不干扰
- **配套 Web 控制台**（开发中）：增删追番、看进度、手动触发

## 架构

```
┌──────────────────────────────────────────────┐
│  Web UI (localhost:8765)                      │
│  vanilla HTML + fetch                         │
└──────────────────────────────────────────────┘
              ↕ fetch 同源
┌──────────────────────────────────────────────┐
│  web_app.py  (Python stdlib http.server)    │
│  ├ /api/series          CRUD 追番列表        │
│  ├ /api/mikan/search    代理蜜柑搜索         │
│  ├ /api/mikan/bangumi   详情页解析            │
│  ├ /api/pan/ls          网盘目录扫描         │
│  └ /                   返回 index.html       │
└──────────────────────────────────────────────┘
              ↕ msvcrt 文件锁
         追番列表.json
              ↑
┌──────────────────────────────────────────────┐
│  auto_dl.py  (定时触发，可由 cron / automation 驱动) │
│  1. 自检 BDUSS (gettemplatevariable)          │
│  2. 失效 → QRCODE_NEEDED（通知用户扫码）     │
│  3. 遍历追番列表 → 抓蜜柑详情页               │
│  4. 解析筛选 → 按字幕组偏好选每话最佳版本     │
│  5. 对比 last_episode → 提交新话到 cloud_dl   │
│  6. 回读任务状态 → 更新清单 → 清理临时文件     │
└──────────────────────────────────────────────┘
              ↕
         百度网盘 web API
```

## 安装

```bash
git clone https://github.com/PAPABISI/mikan-baidu-auto-dl.git
cd mikan-baidu-auto-dl
```

需要 Python 3.11+（仅用标准库，无需 pip install）。

## 使用

### 1. 扫码登录获取 BDUSS

```bash
python qr_cloud_dl.py
```

会生成 `login_qr.png`，用手机百度/网盘 App 扫码即写入 `.baidu_cookies.json`。

### 2. 配置追番列表

编辑 `追番列表.json`：

```json
{
  "series": [
    {
      "mikan_id": 4059,
      "name": "FX战士久留美",
      "subtitle_groups": ["绿茶字幕组"],
      "language": "简体",
      "embedded_only": true,
      "save_path": "/FX战士久留美",
      "last_episode": "0"
    }
  ]
}
```

`mikan_id` 是蜜柑详情页 URL 末尾的数字，如 `https://mikanani.me/Home/Bangumi/4059` → `4059`。

### 3. 手动触发一次

```bash
python auto_dl.py
```

### 4. 定时触发

用 cron 或任意调度器定时执行 `python auto_dl.py`，例如每周一 10:00：

```cron
0 10 * * 1 cd /path/to/mikan-baidu-auto-dl && python auto_dl.py
```

## 项目结构

```
.
├── 追番列表.json               # 配置文件（数据驱动）
├── auto_dl.py                  # 主入口：定时触发
├── qr_cloud_dl.py              # 扫码登录，获取/续命 BDUSS
├── parse_mikan.py              # 蜜柑详情页解析器
├── filelock.py                 # Windows msvcrt 文件锁（web 与 auto_dl 共享）
├── web_app.py                  # Web 后端（stdlib http.server，端口 8765）
├── static/index.html           # Web 前端单页（暗色主题 + 扫码模态）
├── HANDOFF.md                  # 内部交接文档（转 public 前删除）
├── LICENSE
└── README.md
```

## 数据模型

### `追番列表.json`

```json
{
  "series": [
    {
      "mikan_id": 4059,
      "name": "FX战士久留美",
      "subtitle_groups": ["绿茶字幕组"],
      "language": "简体",
      "embedded_only": true,
      "save_path": "/FX战士久留美",
      "last_episode": "03",
      "last_check": "2026-10-18",
      "last_check_status": null,
      "last_check_detail": null,
      "cover_url": null,
      "added_at": "2026-10-25",
      "notes": ""
    }
  ]
}
```

## 运行要求

- Python 3.11+
- 一个能访问 [mikanani.me](https://mikanani.me) 的本地代理（例如 Clash for Windows，监听在 `127.0.0.1:7897`）。蜜柑经常遭到 DNS 污染。
- 百度网盘账号（普通账号即可，会员可享有更大的离线下载配额）
- Cloudflare 不影响

## 已知限制

1. **登录态会过期**：BDUSS 通常几天到几周失效，过期后必须人工扫码。这是百度的设计，无法绕开。
2. **bdstoken 风控**：`gettemplatevariable` 请求 `fields=["bdstoken"]` 单字段会被风控拦截（errno=-6），必须带多字段（如 `["bdstoken","username","dp"]`）。代码已内置此规避。
3. **版权哈希过滤**：热门番剧的磁力可能被百度秒失败或文件改名 `.bak`，无法控制，只能事后如实汇报。
4. **蜜柑依赖页面结构**：HTML 选择器变化会让解析器失效，需要不定期维护。
5. **单进程**：`http.server` 单线程，用线程池处理并发请求。
6. **`baidupcsapi` 不可达**：本仓库之所以从零搭，就是因为 PyPI 上的 `baidupcsapi` 历史项目不可达，且我们走的是 web API 不是 PCS API。

## 失败处理

`auto_dl.py` 写出的 `.auto_dl_state.json` 字段 `status`：

| status | 含义 |
|---|---|
| `QRCODE_NEEDED` | 登录态失效，需扫码 |
| `NET_ERROR` | 网络/代理问题 |
| `NO_UPDATES` | 本周无新话 |
| `OK` | 提交成功 |
| `GENERIC` | 其他错误 |

## 路线图

### 已完成

- [x] 蜜柑详情页解析器
- [x] 扫码登录模块
- [x] 主脚本（自检/抓取/筛选/提交/回读/状态分桶）
- [x] 配置文件设计
- [x] GitHub 仓库初始化
- [x] README + LICENSE
- [x] Web 控制台基础框架（CRUD + 蜜柑搜索 + 详情页解析 + 网盘扫描 + 手动触发）
- [x] 文件锁封装（msvcrt，web 与 auto_dl 共享）
- [x] 前端扫码模态（二维码直接渲染在页面内，轮询状态机驱动）
- [x] 登录链路打通（扫码 + 缓存登录态复用双路径，bdstoken 风控规避）

### 进行中

- [ ] 首次真实提交验证（久留美 01v2/02/03 三话，磁力已就绪、登录态已缓存）
- [ ] Web 控制台优化（话数正则、网盘目录扫描稳定性、UI 细节）
- [ ] 去除 `web_app.py` 中首次提交用的硬编码磁力（改为配置驱动）

### 待办

- [ ] 登录态定时体检
- [ ] 失败重试策略
- [ ] 入口 .bat：双击启动 web + 浏览器

> 内部进度断点与环境细节见 `HANDOFF.md`（仓库转 public 前删除）。

## 借鉴的项目

调研中没有直接复用代码，但借鉴了思路：

- **AutoBangumi** (8.3k★) — 清晰分层：抓取/解析/提交/汇报
- **Alist-MikananiRss** (126★) — LLM 重命名思路（未来可加，让 Emby/Jellyfin 识别）
- **baidu-autosave** — 百度网盘登录态自检（与应用方向不同，不直接用）

## 设计取舍

| 选择 | 原因 |
|---|---|
| 详情页爬取，不用 RSS | RSS 要蜜柑账号订阅，本仓库零账号即可 |
| PyPI 装不了，用标准库 | 实测默认沙箱代理 + Clash 7897 + 系统出口都打不通 PyPI |
| 不依赖外部 BT 客户端 | 目标就是塞进百度网盘，不搭 NAS |
| 不接通知渠道（PushPlus 等） | 由上层调度器（cron/WorkBuddy automation）处理通知 |
| 蜜柑走 Clash 7897，百度直连 | 两个域名互不可达 |

## License

MIT