# 蜜柑片架 · mikan-baidu-auto-dl

从蜜柑寻找番剧资源，由具备浏览器能力的 AI Agent 在已登录的百度网盘官网添加，本地 Web 保存番剧资料与逐话操作历史。Python 脚本负责检索、偏好筛选、去重和记录；网页按钮“检查新集”只准备清单，不会自己操作网盘。

## 当前验证结果（2026-10-30）

- 已实时搜索蜜柑“FX战士久留美”（4059），筛出绿茶字幕组 01v2、02、03、04，均为简日内嵌。
- 已关联 Bangumi 动画 622288：12 话；官方放送日为周四，北京时间 20:30。字幕发布时间根据当前样本推测为周五/周六，不代表字幕组承诺。放送信息：[官方 ONAIR](https://fxkurumi-info.com/onair/)。
- 用户已在内置浏览器登录百度网盘官网。首次目录操作被浏览器安全策略拦截，**没有执行任何云添加，也没有确认任何一话入盘**。队列保留 4 话 READY，Web 显示执行受阻。不能通过替代控制方式绕过该拦截。
- 已按用户选择接入本聊天每天北京时间 10:00 的资源巡检，4 话保留待添加。当前任务只检查和维护清单，不领取、不提交，也不主动恢复网盘执行；以后用户另行要求恢复时再评估。实际调度以应用中的任务状态为准。
- 原 `last_episode=03` 按先前要求保留；它不是新目录已有文件的证据，不会让新清单跳过前三话。

## 启动 Web

需要 Python 3.10+，只用标准库，无需 pip。Windows 双击 `start.bat`，或：

```powershell
python web_app.py --open
# 本轮调试地址
python web_app.py --port 8766
```

默认从 http://127.0.0.1:8765 启动；端口占用会顺延，以终端打印地址为准。只监听本机，按 Ctrl+C 关闭。保持运行才能查看 Web；CLI 和 Agent 调度可直接读写数据，不依赖 Web 服务常驻。

Web 提供：

- 番剧封面、简介、制作方、总话数、字幕组与语言偏好。
- 每周放送日与字幕发布日推测，分开显示。
- 每话资源标题、磁力、保存目录、状态与具体证据。
- 按时间排列的操作历史，以及 JSON 导出。
- 搜索蜜柑、关联 Bangumi、编辑追番偏好、重试明确失败的任务。
- “记录手动结果”：先在官网操作，再选择实际结果并填写文件名、目录或任务提示；这类记录标明“用户确认”。受理和完成要分别记录。

每次 Agent 回报或手动保存后立即写入数据，页面约 5 秒内刷新。Web 不展示或导出百度凭据。

## 网络与目录

蜜柑与 Bangumi 默认走 `http://127.0.0.1:7897`。代理需自行开启，可在启动前设置：

```powershell
$env:MIKAN_PROXY = 'http://127.0.0.1:7897'
$env:BANGUMI_PROXY = 'http://127.0.0.1:7897'
```

空字符串表示直连。百度登录及云添加由 Agent 浏览器按其自身网络设置执行，程序不提取登录 Cookie。

所有新任务保存到 `/Anime/<番名>`。在官方 UI 中核验并创建缺少的子目录，已有目录直接复用。久留美目标为 `/Anime/FX战士久留美`。添加前检查已有文件与任务，防止重复。

## AI Agent 接入

完整协议见 [AGENTS.md](AGENTS.md)；Claude Code 可从 [CLAUDE.md](CLAUDE.md) 进入。任何能访问同一目录、运行 Python 并操作浏览器 UI 的 Agent 都可以按协议接入；不同 Agent 的浏览器登录态不保证共享，只有命令行、没有浏览器能力的 Agent 只能准备清单。

```powershell
python agent_cli.py search 'FX战士久留美' --source mikan
python agent_cli.py search 'FX战士久留美' --source bangumi
python agent_cli.py watch --mikan-id 4059 --name 'FX战士久留美' --group '绿茶字幕组' --bangumi-id 622288
python agent_cli.py prepare
python agent_cli.py queue
python agent_cli.py library
```

确认没有工具策略阻碍、已登录且目标目录正确之后，Agent 才领取一话：

```powershell
python agent_cli.py claim --agent '你的 Agent 名称'
python agent_cli.py report --input report.json
python agent_cli.py review --input review.json
python agent_cli.py observe --input observation.json
```

所有输出为 UTF-8 JSON。`report/review/observe` 也支持 stdin；建议使用 UTF-8 JSON 文件，避免 shell 转义出错。命令成功退出 0，参数/执行错误退出 2；prepare 遇到网络错误或忙碌也退出 2，不能把它说成没有更新。

report 示例：

```json
{"job_id":"领取结果的id","token":"领取结果的claim_token","state":"SUBMITTING","evidence":"官网已填该话磁力并确认保存目录，准备点击最终提交"}
```

review 用于回读已受理/下载中/未知任务，不需要领取令牌，包含 job_id、state、evidence。

observe 用于真实存在的文件或人工添加结果，不声称 Agent 执行了提交：

```json
{"job_id":"队列中的id","state":"COMPLETED","source":"user","evidence":"用户明确确认 /Anime/番名/实际文件名.mp4 已完整入盘"}
```

`source=user` 表示用户确认，`source=web` 表示 Agent 亲自观察官网。没有对应证据时不能运行此示例。正在领取或提交的任务不能被观察记录覆盖。

## 任务状态与去重

| 状态 | 含义 |
|---|---|
| READY | 已筛选，尚未提交 |
| CLAIMED | Agent 已领取，尚未点击最终提交 |
| SUBMITTING | 最终点击前已持久记录，下一步有远端副作用 |
| SUBMITTED | 官网明确受理，尚未确认完成 |
| DOWNLOADING | 官网显示下载中 |
| COMPLETED | 实际文件/任务已核验完成，或用户明确确认完成 |
| FAILED | 明确失败，可在核验未重复后重试 |
| UNKNOWN | 可能已提交，必须先核验，不自动重提 |
| SUPERSEDED | 未提交资源被更合适的版本替换 |

领取租约 15 分钟。未开始提交就中断的任务可在租约到期后重新领取；已开始提交的中断变为 UNKNOWN。已受理、下载中、未知与已入盘的话数不会因为新 hash、v2 或资源消失而重新添加。文件锁与原子替换保护多个 Agent 的本地写入。

筛选优先简体内嵌，“简日内嵌”符合；“简繁”不自动等同于“简体”。字幕组按配置顺序优先，同组同话选择最高修订版。合集、SP、OVA、特别篇不自动入队。旧历史游标仅保留，不作为新库确认结果。

## 定时任务

本轮选择：北京时间每天 10:00，在当前聊天继续巡检。已有应用调度，无需另建 Windows 计划任务。首次验证发现的浏览器阻碍已经写入执行状态，存在时保持资源检查模式；不能把“每天检查”描述为“每天已自动下载”。

本机任务需要电脑与应用运行、工作目录存在且代理可用；参考 [官方定时任务文档](https://learn.chatgpt.com/docs/automations?surface=app)。无变化时安静；有新资源、添加结果、错误或新的必要用户操作才报告，同一个已知阻碍不每天重复通知。

其他 Agent 可用自身调度器每天运行相同协议。单独定时运行 `prepare` 只生成队列，完整云添加还需要该 Agent 的浏览器执行与逐话回报。不要同时部署互不知晓的重复调度；已有任务会由共享库去重。

## 本地 HTTP API

写请求使用 JSON，同源/本机限制已启用。

| 路径 | 用途 |
|---|---|
| GET /api/library | Web 资料、任务、最近 200 条事件、执行和调度状态 |
| GET /api/library/export | 完整片库及全部事件，无领取令牌 |
| GET /api/agent/queue | 任务列表，调用者按 state 过滤 |
| GET /api/agent/guide | Agent 接入协议 |
| POST /api/agent/prepare | 异步准备资源，202 启动，409 忙碌 |
| POST /api/agent/claim | body: agent、可选 job_id |
| POST /api/agent/report | body: job_id、token、state、evidence |
| POST /api/agent/review | body: job_id、state、evidence |
| POST /api/agent/observe | body: job_id、state、source、evidence |
| POST /api/agent/retry | body: job_id、evidence，仅明确失败可重试 |
| GET /api/mikan/search?q= | 蜜柑搜索 |
| GET /api/mikan/bangumi/{id} | 字幕组与资源分布 |
| GET /api/bangumi/search?q= | 仅动画条目搜索 |
| POST /api/metadata/{mikan_id} | 资料更新，或 body: bangumi_id |
| GET/POST /api/series | 获取/新增追番 |
| PUT/DELETE /api/series/{index} | 编辑/移除配置，附 mikan_id 校验 |
| GET /api/state | 最近资源检查结果 |

## 数据、迁移与旧代码

### 跨设备与跨 Agent 进度同步

当前没有跨设备自动同步：`追番列表.json` 随 Git 同步的是追番配置，`data/library.json` 保存的实际受理、下载和入盘状态不上传 Git。仅执行 git pull 不会取得另一台设备的入盘记录。Bangumi 当前只用于读取番剧资料。

同一台机器、同一工作目录下的多个 Agent 可通过 CLI 或本地 HTTP 使用同一个片库，已有文件锁与领取机制避免重复提交。迁移到另一台机器时，应先停止两边的写入，备份并成对复制 `追番列表.json` 与 `data/library.json`；仅导出的 Web 视图不等同于可直接覆盖使用的原始库。迁移后需重新在该 Agent 的浏览器登录，定时任务也需单独配置。

多台机器同时工作需要另行接入统一的受控记录服务，统一处理领取、去重与状态更新。目前文件锁只保护同一文件系统上的写入，不能保证云同步目录或两份独立副本之间的并发安全。不要通过公开仓库提交真实记录、领取令牌或登录凭据来替代共享服务。

- `追番列表.json`：追番配置。
- `data/library.json`：唯一新片库，持久保存元数据、每话任务和完整事件。已 Git 忽略，迁移时需自行备份；片库页面和普通接口展示最近 200 条事件，Web 导出包含全部事件。
- `catalog.py`：任务、领取、状态转换、事件事务。
- `agent_bridge.py` / `agent_cli.py`：准备资源与跨 Agent JSON 协议。
- `metadata.py` / `parse_mikan.py`：Bangumi 资料与蜜柑解析。
- `web_app.py` / `static/`：本地 Web。
- `auto_dl.py`、`baidu.py`、`qr_cloud_dl.py`：保留早期接口实验与回归测试。**不要用旧入口执行当前追番**；旧 `.task_history.json` 与 Cookie 缓存不代表新库已入盘。

新 Web 启动时不执行旧登录监测。旧扫码和百度 API 路由仅为兼容保留，不在当前 UI 中使用。运行时凭据、记录、锁、二维码和诊断文件均不能提交 Git。新机器需重新在 Agent 自己的浏览器登录，不复制浏览器 Cookie 数据库。

## 开发验证

```powershell
python -m unittest discover -s tests -v
node --check static/app.js
```

测试使用临时配置与模拟远端，不提交真实网盘任务。覆盖筛选、并发领取、重复提交保护、中断、人工观察记录、证据和 HTTP 路由。Node 仅用于开发检查；运行时不依赖 Node。

蜜柑 HTML 和网盘 UI 可能变化；会员额度、资源处理结果与风控由百度决定。登录失效或安全验证需要用户介入。当前真实端到端添加验证受浏览器策略阻碍，不能声称已完成下载。

MIT License。
