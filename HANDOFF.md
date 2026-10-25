# HANDOFF — AI 接手交接文档

> 本文档面向下一个接手本项目的 AI 助手，包含所有不在 README 里的环境细节、踩坑记录和当前断点。README 是面向 GitHub 的公开说明，本文档是**内部工作文档**，请先读完本文再动代码。
>
> 最后更新：2026-10-25

## 一、项目现状快照

| 项 | 状态 |
|---|---|
| 仓库 | `https://github.com/PAPABISI/mikan-baidu-auto-dl.git`（Private，用户：PAPABISI） |
| 工作目录 | `C:/Users/u00uk/WorkBuddy/2026-10-18-11-59-20/` |
| 核心流水线 | ✅ 可用：蜜柑抓取 → 筛选 → cloud_dl 提交 → 回读 |
| Web 控制台 | ✅ 可用：localhost:8765，CRUD + 蜜柑搜索 + 网盘扫描 + 扫码模态 |
| 登录链路 | ✅ 已打通：扫码 + 缓存复用两条路（见下"百度风控"） |
| 定时任务 | ✅ WorkBuddy automation，每周一 10:00（ID: `82fae53d-e045-49e8-84e3-a80e23bdea42`，ACTIVE） |
| **当前断点** | ⚠️ 首次真实提交未完成最终验证（详见第五节） |

## 二、网络拓扑（关键！）

接手的 AI 运行在沙箱里，网络受限，**所有外部请求必须显式走代理**：

```
沙箱默认代理 127.0.0.1:9104（sandbox-cli.exe）
  ├─ 域名白名单：github / npm / pypi / x.com 等放行
  └─ google / wikipedia / huggingface 等拦截（返回 502）

Clash 127.0.0.1:7897（verge-mihomo.exe，用户本机 ClashVerge 规则模式）
  └─ 完整公网访问，蜜柑必须走这里
```

- **蜜柑（mikanani.me）**：`urllib.request.ProxyHandler({"http": "http://127.0.0.1:7897", "https": ...})`
- **百度（pan.baidu.com / passport.baidu.com）**：`ProxyHandler({})` 直连，走代理反而可能失败
- **代理 URL 必须写域名，不能写 IP**（沙箱代理按域名分流）
- 本机 DNS 被污染（www.google.com 曾解析到 Facebook IP），不要信系统解析结果
- **PyPI 不可达**：全套代码只用 Python 标准库，不要尝试 pip install

## 三、百度网盘接口踩坑记录（血泪史）

1. **bdstoken 风控（最重要）**：
   `GET /api/gettemplatevariable?fields=["bdstoken"]` 单字段请求会被风控拦截（`errno=-6`，即使登录态完全有效）。
   **必须请求多字段**：`fields=["bdstoken","username","dp"]`（URL 编码后拼在 query 上）。
   这是本项目最后一个大坑，已在 `web_app.py` 和 `qr_cloud_dl.py` 中修复。

2. **提交离线任务**：
   `POST https://pan.baidu.com/rest/2.0/services/cloud_dl?method=add_task&bdstoken=<token>`
   body: `source_url=<url编码的magnet>&save_path=<路径>&type=2`

3. **回读任务**：同接口 `method=list_task`，按 task_id 查状态（进度/失败）。

4. **扫码登录链路**（`qr_cloud_dl.py` / `web_app.py` 已实现）：
   `passport.baidu.com/v2/api/getqrcode` 拿二维码 → `channel/unicast` 长轮询 → `v3/login/main/qrbdusslogin` 换 BDUSS。
   拿到后缓存 `.baidu_cookies.json`（BDUSS/STOKEN/PTOKEN）。

5. **登录态自检**：优先 `user/getinfo`，或用多字段 `gettemplatevariable`。失效才重新扫码。

6. **MCP connector 不可用**：百度网盘 MCP 的 16 个接口里没有 cloud_dl 离线下载，这就是自己写 web API 调用的原因。

7. **浏览器自动化路线已废弃**（不要重试）：
   - Chrome 155 v20 app-bound 加密：密钥绑定 Chrome 安装路径+原始配置目录，复制出来的配置 Chrome 自己都解不出（加载 0 条 cookie）
   - Chrome 禁止对默认配置目录开 `--remote-debugging-port`
   - Edge 运行中锁 Cookies 文件（WinError 32）
   - 结论：直接走扫码 + cookie 缓存，别碰浏览器配置。

## 四、Web 控制台

- 启动：`python web_app.py`（127.0.0.1:8765，stdlib http.server）
- 前端：`static/index.html`，暗色主题单页，与后端全部同源 fetch
- 扫码流程：前端模态框显示二维码（`static/qr.png`）→ 轮询 `/api/qr/status` → 状态机 `IDLE / WAITING / LOGGED_IN / SUBMITTING / DONE / FAILED`
- **注意**：`web_app.py` 里的 `DEFAULT_MAGNETS`（久留美三话磁力）和 `DEFAULT_SAVE_PATH` 是**临时硬编码**的首次提交测试用，正式化时应改为从 `追番列表.json` 驱动。这是已知技术债。

API 一览：
- GET: `/api/series`, `/api/state`, `/api/cookies`, `/api/mikan/search?q=`, `/api/mikan/bangumi/{id}`, `/api/pan/ls?path=`, `/api/qr/status`
- POST: `/api/series`, `/api/refresh`, `/api/scan-qr`
- PUT / DELETE: `/api/series/{idx}`

## 五、当前断点（接手后第一件事）

用户目标：把 #4059 FX战士久留美 目前更新的 3 话（绿茶字幕组简体内嵌）提交到网盘 `/FX战士久留美/`。

磁力清单（已存网盘 `/FX战士久留美/简体内嵌磁力清单.txt`）：
- 第01话v2 (432.8MB): `magnet:?xt=urn:btih:8c67c88e94481c38ac2302b74f65534937dc1ae5`
- 第02话 (481.8MB): `magnet:?xt=urn:btih:44973df486878fbaf21762b66cdf139c6c58f6bf`
- 第03话 (479.0MB): `magnet:?xt=urn:btih:0f57ffc34048fae847998f3f4ff18a8d87f9ec1d`

**进展**：用户已扫码成功，`.baidu_cookies.json` 缓存了有效登录态（超级会员账号）；`web_app.py` 的 `_qr_login_thread` 已改造为"优先复用缓存登录态、失效才扫码"，并修复了 bdstoken 多字段风控问题。

**未完成**：改造后服务被重启但流程被用户中断，`/api/scan-qr` 尚未在修复后的代码上完整跑通一次。

**接手步骤建议**：
1. 确认 `.baidu_cookies.json` 存在且登录态有效（用 `user/getinfo` 自检）
2. 重启 `python web_app.py`
3. 调 `POST /api/scan-qr`（应跳过扫码直接提交 3 条磁力）
4. 轮询 `/api/qr/status` 看任务回读结果
5. 若 task 失败（哈希过滤），如实告知用户，别硬造成功

**已知风险**：百度对热门版权番做哈希过滤，任务可能"提交成功但秒失败"或文件被改名 `.bak`，无法控制，只能事后汇报。

## 六、开发约定

1. 所有外部 API（蜜柑、百度）走 web 后端代理调用，前端只跟 localhost 通信
2. 写 `追番列表.json` 必须用 `filelock.py`（msvcrt Windows 文件锁，web 与 auto_dl 共享）
3. 运行时产物（`login_qr.png`、`static/qr.png`、`.auto_dl_state.json`、`.qr_state.json`、`.qr_login.log`）全部 gitignore
4. 通知只走 WorkBuddy automation，不接 PushPlus 等外部渠道
5. 代码提交到 main 分支，三步：`git add -A; git commit -m "..."; git push`
6. Python 依赖：**零外部依赖**，只用标准库（PyPI 不可达）
7. commit message 用中文，格式 `feat:/fix:/docs:` 前缀

## 七、下一步 TODO（按优先级）

1. **跑通首次提交验证**（第五节）
2. 把 `DEFAULT_MAGNETS` 硬编码改为从 `追番列表.json` 驱动（去掉技术债）
3. 话数提取正则加固（`parse_mikan.py`，v2/特别话等边缘 case）
4. 登录态定时体检 + 失效自动引导用户扫码（而非等到提交时才发现）
5. 入口 `.bat`：双击启动 web + 自动开浏览器
6. 版权哈希过滤的兜底汇报文案优化

## 八、关键文件速查

```
追番列表.json        配置驱动（含 last_episode 增量游标）
auto_dl.py           周级主入口（自检→抓取→筛选→提交→回读→更新）
qr_cloud_dl.py       独立扫码登录脚本
parse_mikan.py       蜜柑详情页解析（可导入模块）
filelock.py          Windows msvcrt 文件锁
web_app.py           Web 后端 + 扫码线程 + 磁力提交
static/index.html    Web 前端单页
.baidu_cookies.json  登录态缓存（gitignore，绝不能提交）
.auto_dl_state.json  上次运行状态（gitignore）
.qr_state.json       扫码会话状态持久化（gitignore）
```
