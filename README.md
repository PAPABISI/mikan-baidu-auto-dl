# 蜜柑片架 · mikan-baidu-auto-dl

从蜜柑寻找番剧资源，由具备浏览器能力的 AI Agent 在已登录的百度网盘官网添加，本地 Web 保存番剧资料与逐话操作历史。Python 脚本负责检索、偏好筛选、去重和记录；网页按钮“检查新集”只准备清单，不会自己操作网盘。

## 开发与 AI 接手

开发流程、模块边界、测试和 Git 规范见 [DEVELOPMENT.md](DEVELOPMENT.md)，Agent 执行约束见 [AGENTS.md](AGENTS.md)。本项目当前优先本地个人使用；每天采集完整资源目录、SQLite 和独立观看记录的可行性与后续方向见 [docs/ROADMAP.md](docs/ROADMAP.md)，这些规划尚未实施。每次新增需求完成并通过必要验证后自动创建 Git 提交。

## 当前验证结果（2026-10-30）

- 已实时搜索蜜柑“FX战士久留美”（4059），筛出绿茶字幕组 01v2、02、03、04，均为简日内嵌。
- 已关联 Bangumi 动画 622288：12 话；按 [Bangumi 条目的放送星期](https://bgm.tv/subject/622288) 排在周四。字幕发布时间根据当前样本推测为周五/周六，仅作资源参考，不参与每周日历，也不代表字幕组承诺。
- 用户已在内置浏览器登录百度网盘官网。首次目录操作被浏览器安全策略拦截，**没有执行任何云添加，也没有确认任何一话入盘**。队列保留 4 话 READY，Web 显示执行受阻。不能通过替代控制方式绕过该拦截。
- 已按用户选择接入本聊天每天北京时间 10:00 的资源巡检，4 话保留待添加。当前任务只检查和维护清单，不领取、不提交，也不主动恢复网盘执行；以后用户另行要求恢复时再评估。实际调度以应用中的任务状态为准。
- 原 `last_episode=03` 按先前要求保留；它不是新目录已有文件的证据，不会让新清单跳过前三话。

## 启动 Web

需要 Python 3.10+，只用标准库，无需 pip。Windows 双击 `start.bat`，或：

```powershell
python web_app.py --open
# 指定端口调试
python web_app.py --port 8786
```

默认从 http://127.0.0.1:8785 启动；端口占用会顺延，以终端打印地址为准。只监听本机，按 Ctrl+C 关闭。保持运行才能查看 Web；CLI 和 Agent 调度可直接读写数据，不依赖 Web 服务常驻。当前工作副本使用 8785，避免与其他副本占用的 8765/8766 冲突；多个副本同时运行时以各自终端打印的地址为准。

更新 Python 代码后，先在旧服务控制台按 Ctrl+C，再运行 `start.bat`；刷新网页不会重新加载后台代码。Windows 服务使用独占端口，避免多个版本同时监听同一端口。多次启动时仍以新控制台打印的地址为准。合集界面会检测旧版接口并提示重启，不能把缺少合集字段当作“没有合集”。

Web 提供：

- 番剧封面、简介、制作方、总话数、字幕组与语言偏好。
- “每周更新”仅按 Bangumi 条目的放送星期排期，可点击查看来源；不从首播日期、字幕发布时间推算或按时区挪动星期。缺少资料的番剧单列“放送日待补充”。字幕发布日推测仅在片库卡片中显示作参考。
- 每话资源标题、磁力、保存目录、状态与具体证据。
- 按时间排列的操作历史，以及 JSON 导出。
- 搜索蜜柑、关联 Bangumi、编辑追番偏好、重试明确失败的任务。
- 新增与编辑追番均可下拉选择字幕组，再选择简体/繁体/简繁、内嵌/外挂/内封及清晰度；版本按钮显示可用话数，并可一键填入这些条件。
- 支持识别 `01-14 合集`、区间、全集及 Batch/Complete 等合集标记。只有合集的字幕组也可选；版本分别显示单话数和合集数，并提供“复制合集磁力”。日常检查会将合集保存在独立候选区，详情提示字幕偏好不符及与已有任务的重叠，不把一个合集计为一话或虚构多话已入盘。
- 新增/编辑追番会展示全部字幕组的合集，即使默认选中另一组的简体内嵌版本也能发现；“选用此合集的字幕版本”会明确切换字幕组、语言、字幕形式与清晰度，未点击时保留原偏好。
- “季度新番”可选择年份、季度，按热度/评分/排名浏览 Bangumi 公开动画，显示评分人数、简介与首播日期，支持分页、季度内搜索和从条目转到蜜柑找资源。
- “记录手动结果”：先在官网操作，再选择实际结果并填写文件名、目录或任务提示；这类记录标明“用户确认”。受理和完成要分别记录。
- “核实入盘”：番剧详情支持逐话多选、全选及取消全选；全选仅包含当前番剧可核实的任务，执行中、已入盘和已替换任务不参与。核对确认窗口中的话数、资源和目录，填写适用于每一项的实际证据后保存；每话分别保留用户确认及历史。结果不同请分批记录，任一任务状态冲突时整批不保存。

每次 Agent 回报或手动保存后立即写入数据，页面约 5 秒内刷新。Web 不展示或导出百度凭据。

季度按北京时间当前年月默认选择，冬/春/夏/秋分别对应 1–3/4–6/7–9/10–12 月；筛选的是首播或发行日期，因此包含 TV、WEB、剧场版及 OVA，不包含上季首播后本季续播的作品，也不能列出 Bangumi 未公开的条目。每页 20 条，翻页可浏览后续结果；评分人数为 0 时显示“暂无评分”。公开数据缓存 30 分钟，“刷新评分”可主动更新，远端失败时有缓存会明确标为旧数据。[Bangumi 官方 API 定义](https://github.com/bangumi/api/blob/master/open-api/v0.yaml)。

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
# 明确限定字幕组、字幕形式和清晰度
python agent_cli.py watch --mikan-id 4059 --name 'FX战士久留美' --group '绿茶字幕组' --language '简体' --subtitle-type '内嵌' --resolution 1080p --only-group
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

具备网盘只读能力的 Agent（例如已连接百度网盘连接器）可以自动核验入盘：先读取 `/Anime/<番名>` 目录的文件清单，再调用 verify 接口。程序按话数把文件与任务唯一匹配，仅对无歧义且允许覆盖的状态（READY/SUBMITTED/DOWNLOADING/UNKNOWN/FAILED）写入 COMPLETED，证据为真实文件名与大小；同一话出现多个文件（如 01 与 01v2 共存）或多个候选任务时只报告歧义，不写入。

```powershell
# verify.json：{"save_path":"/Anime/FX战士久留美","files":[{"filename":"[Studio GreenTea] ... [01v2] ....mp4","size":453786047}]}
python agent_cli.py verify --input verify.json
python agent_cli.py verify --input verify.json --dry-run
```

清单文件由 Agent 的网盘只读工具提供，本程序不访问网盘；清单为空或不合法时报错，不据此改写记录。核验自动化不改变云添加流程：连接器没有离线下载接口，磁力提交仍需在官网手动完成。

### 合集入盘核验

合集没有逐话任务，因此网盘里出现合集文件（或合集解压出的 01–12 单话文件）时，核验会为**真实存在的话数**建立已入盘记录：`origin=collection`、`collection_id` 指向候选合集，证据同时写明真实文件名与合集标题。规则：

- 只处理没有任务、或被合集覆盖的话数；执行中（CLAIMED/SUBMITTING）、已入盘或已替换的记录绝不改写，只列入 `skipped_collection_episodes`
- 同一话能被多个合集候选覆盖时**必须指定合集**，否则只返回 `collection_choices` 候选、不写入，避免把文件记到别的字幕版本上
- 合集范围里网盘还没有文件的剩余话数写入 `collection_missing_episodes`，不虚构进度
- `--dry-run` 只输出计划；`collection_id` 没有覆盖任何文件时整批报错、不保存

```powershell
# 多个候选时用 --collection 指定合集编号或 infohash
python agent_cli.py verify --input verify.json --collection b9519f364e5331f99eb9ac4b2430d5bbc8ef17af

# 用户自己在网盘看到文件、由 Agent 记录（也可在 Web 合集卡上点“记录该合集入盘”）
python agent_cli.py collection-observe --input collection.json
# collection.json：{"collection_id":"c-xxxx","episodes":["01","02"],"evidence":"网盘可见这两个文件"}
```

Web 端合集卡新增“记录该合集入盘”：默认按合集范围预填话数，可改成实际已入盘的话数（如 `1-12`、`1,2,5`），保存后逐话写入 `source=user` 的入盘记录。

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

筛选优先简体内嵌，“简日内嵌”符合；“简繁”不自动等同于“简体”。字幕组按配置顺序优先，同组同话选择最高修订版。合集独立展示供手动选择，不进入普通单话队列；SP、OVA、特别篇不自动入队。合集范围来自标题，范围未标明时保留待核实，不能由 Bangumi 总话数推定。旧历史游标仅保留，不作为新库确认结果。

例如 [冰之城墙（3898）](https://mikanani.me/Home/Bangumi/3898) 的 LoliHouse 版本为 `01–14 合集 / 简繁内封 / 1080p`，可在新增或编辑追番中选择 LoliHouse 查看、复制磁力；它不符合默认简体内嵌偏好，需由用户明确选择该字幕版本。合集仍是资源候选：发现时不建逐话任务、不推进进度，也不能通过单话 report/observe 虚构合集已入盘；只有网盘实际可见的文件（或用户在 Web 合集卡确认）才写入 `origin=collection` 的入盘记录，见上文“合集入盘核验”。

新配置可使用 `subtitle_type`（内嵌/外挂/内封/不限）、`resolution`（2160p/1440p/1080p/720p/480p/未知/不限）及 `strict_groups`。勾选“仅使用所选字幕组”时不能用其他组补齐；取消后字幕组作为优先级。旧配置仍按 `embedded_only` 解释字幕形式，默认不限清晰度。修改偏好后，下次成功检查会将不再匹配的 READY 资源标为 SUPERSEDED；已受理或已入盘记录不重写、不自动重下。没有匹配资源时可以保存偏好等待更新。

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
| POST /api/agent/observe-batch | body: job_ids（非空且不重复）、state、source、evidence；同一番剧和目录，全部成功或全部不保存，返回 jobs |
| POST /api/agent/verify | body: save_path、files（网盘文件清单）、source（默认 web）、dry_run、collection（可选合集编号或 infohash）；按话数匹配任务并记录入盘证据，为无任务但被合集覆盖的话数建立入盘记录 |
| POST /api/collections/observe | body: collection_id、episodes（合集范围内的话数）、source（默认 user）、evidence；逐话写入用户确认的入盘记录，返回 recorded/skipped |
| POST /api/agent/retry | body: job_id、evidence，仅明确失败可重试 |
| GET /api/mikan/search?q= | 蜜柑搜索 |
| GET /api/mikan/bangumi/{id} | 字幕组与资源分布；count/episodes 仍为单话，collection_count/collections 提供合集及磁力；collection_support=true 标记后台支持合集 |
| GET /api/bangumi/search?q= | 仅动画条目搜索 |
| GET /api/bangumi/season?year=2026&quarter=4&page=1&sort=heat&q= | 季度公开动画分页与评分，sort 支持 heat/score/rank，refresh=1 强制更新 |
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
- `verify.py`：网盘目录清单与任务/合集的匹配核验；不访问网络，不提交网盘。
- `agent_bridge.py` / `agent_cli.py`：准备资源与跨 Agent JSON 协议。
- `metadata.py` / `parse_mikan.py`：Bangumi 资料与蜜柑解析。
- `season.py` / `static/season.js`：季度新番浏览；公开资料缓存为 `data/season-cache.json`，不进入 Git，不替代真实入盘记录库。
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
