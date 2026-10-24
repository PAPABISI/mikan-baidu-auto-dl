# 蜜柑追番 + 百度网盘自动化

每周一 10:00 自动从蜜柑检查追番列表中的新集，筛选偏好的字幕组与语言，调用百度网盘离线下载接口入盘。配套 Web 控制台用于增删追番、看进度、手动触发。

---

## 项目结构

```
.
├── 追番列表.json              # 配置文件（数据驱动）
├── auto_dl.py                 # 主入口：每周触发，调百度离线下载接口
├── qr_cloud_dl.py             # 扫码登录，获取/续命 BDUSS
├── parse_mikan.py             # 蜜柑详情页解析器（独立可复用）
├── web_app.py                 # Web 后端（待实现）⚠
├── static/index.html          # Web 前端（待实现）⚠
├── .gitignore
├── README.md                  # 本文件
└── .workbuddy/                # 工作区元数据（已 gitignore）
```

## 架构

```
┌──────────────────────────────────────────────┐
│  Web UI (localhost:8765)                      │  ← 用户浏览器
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
│  auto_dl.py  (automation 每周一 10:00 触发)   │
│  1. 自检 BDUSS (gettemplatevariable)          │
│  2. 失效 → QRCODE_NEEDED（通知用户扫码）     │
│  3. 遍历追番列表 → 抓蜜柑详情页               │
│  4. 解析筛选 → 按字幕组偏好选每话最佳版本     │
│  5. 对比 last_episode → 提交新话到 cloud_dl   │
│  6. 回读任务状态 → 更新清单 → 清理临时文件     │
└──────────────────────────────────────────────┘
              ↕
┌──────────────────────────────────────────────┐
│  百度网盘（BDUSS 直连，走 web API）           │
│  ├ /api/gettemplatevariable → 自检 + bd(tok) │
│  └ /rest/2.0/services/cloud_dl → 任务调度     │
└──────────────────────────────────────────────┘
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
      "notes": "绿茶字幕组唯一三话齐全，01有v2修正版"
    }
  ]
}
```

## 运行时网络

- **蜜柑**：走 Clash `127.0.0.1:7897`（详见 [网络拓扑](#网络拓扑)）
- **百度网盘接口**：直连
- **前端 → 后端**：localhost 同源

## 网络拓扑

```
Web 后端进程 ──┬──→ Clash 7897 ──→ 蜜柑
              └──→ 直连 ────→ 百度网盘 web API
                                            ↑
                                   .baidu_cookies.json 里的 BDUSS
```

## 关键技术决定

1. **零外部依赖**：PyPI 不可达，全程 Python 标准库实现
2. **登录态策略 B**：缓存 BDUSS 在 `.baidu_cookies.json`（gitignored），跑前自检，失效才扫码
3. **文件锁**：web_app.py 和 auto_dl.py 都改 `追番列表.json`，用 `msvcrt.locking` 排他锁
4. **状态分桶**：`QRCODE_NEEDED` / `NET_ERROR` / `NO_UPDATES` / `OK` / `GENERIC`，便于 automation 做差异化通知
5. **automation 仅做触发**：实际工作由本地 Python 脚本完成，automation 触发后读取 `.auto_dl_state.json` 向用户汇报

## 安装与运行

### 一次性：扫码登录获取 BDUSS

```bash
python qr_cloud_dl.py
```

会生成 `login_qr.png`，手机百度/网盘 App 扫码即写入 `.baidu_cookies.json`。

### 手动触发一次追番流水线

```bash
python auto_dl.py
```

### 启动 Web 控制台

```bash
python web_app.py
# 默认监听 http://localhost:8765
```

### automation（已挂载）

- **名称**：每周追番自动化
- **触发**：`FREQ=WEEKLY;BYDAY=MO;BYHOUR=10;BYMINUTE=0`
- **automation ID**：`82fae53d-e045-49e8-84e3-a80e23bdea42`

## 进度记录

### 已完成

- [x] 蜜柑详情页解析器（`parse_mikan.py`）
- [x] 扫码登录模块（`qr_cloud_dl.py`，纯标准库）
- [x] 主脚本自检、抓取、筛选、提交、回读、状态分桶（`auto_dl.py`）
- [x] 配置文件设计（`追番列表.json`）
- [x] automation 挂载，下次触发约一周后

### 进行中

- [ ] Web 控制台（`web_app.py`、`static/index.html`）

### 待办

- [ ] 文件锁封装（msvcrt）
- [ ] 入口 `.bat`：双击启动 web + 浏览器
- [ ] 登录态失效检测：定时体检，提前通知
- [ ] 失败重试策略：QRCODE_NEEDED 不算失败，NET_ERROR 自动重试一次

## 已知限制

1. **登录态会过期**：BDUSS 通常几天到几周失效，过期后必须人工扫码。这是百度的设计，无法绕开。
2. **版权哈希过滤**：热门番剧的磁力可能被百度秒失败或文件改名 `.bak`，无法控制，只能事后如实汇报。
3. **蜜柑依赖页面结构**：HTML 选择器变化会让解析器失效，需要不定期维护。
4. **单进程**：`http.server` 单线程;用线程池处理并发请求。
5. **网盘子目录列目录有时 errno:1002**：MCP connector 限制，web 后端用 web API 直连规避。

## 失败处理

`auto_dl.py` 写出的 `.auto_dl_state.json` 字段 `status`：

| status | 含义 | automation 行为 |
|---|---|---|
| `QRCODE_NEEDED` | 登录态失效 | 通知用户扫码 |
| `NET_ERROR` | 网络/代理问题 | 通知用户检查 Clash |
| `NO_UPDATES` | 本周无新话 | 简要说明 |
| `OK` | 提交成功 | 列出每话 errno 和 task_status |
| `GENERIC` | 其他错误 | 通知并附错误信息 |

## 浏览器/Chrome 调试的限制（历史教训）

- Chrome 155 cookie 用 v20 app-bound 加密，绑定安装路径+原始配置目录；复制配置到别处后 Chrome 自身也解不出 cookie，CDP 导出路线作废。
- Chrome 禁止对默认配置目录开 `--remote-debugging-port`，真身接管也不可行。
- Edge 仍用 v10，可纯本地解密；但用户需要先在 Edge 登录且 Edge 关闭（cookie 文件不被进程占用）。
- 当前生产用的路线是 passport.baidu.com 扫码登录，绕开上述所有浏览器侧问题。

## 维护手册

### 加新番

在 Web 上搜索 → 选字幕组 → 保存。或直接编辑 `追番列表.json` 加对象。
新增后下次周一才会被自动追，要立即抓可在 Web 上点"立即检查"。

### 换字幕组偏好

编辑 `追番列表.json` 对应条目的 `subtitle_groups` 数组。

### 退坑

Web 上点退坑，或从 `追番列表.json` 删对象。**不会**删除网盘里已下载的文件。

### 登录态续命

收到 QRCODE_NEEDED 通知后：
1. `python qr_cloud_dl.py` 生成 `login_qr.png`
2. 手机扫码即写入 `.baidu_cookies.json`