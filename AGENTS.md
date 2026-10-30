# 蜜柑片架：浏览器 Agent 协作协议

本项目当前采用 **Python 准备资源 + AI 操作百度官网 + 本地 Web 记录历史**。
旧的 `qr_cloud_dl.py` / 百度 `cloud_dl` API 方案不作为生产入口。不要再读取浏览器 Cookie 数据库、导出账号凭据或用隐藏网页状态/脚本绕过官方 UI。

## 开发文档与任务范围

- 接手开发先读 [DEVELOPMENT.md](DEVELOPMENT.md)，按其中的实现、验证、文档、Git 提交流程操作；用 README 核对当前接口，用 HANDOFF 顶部了解最近断点。
- [docs/ROADMAP.md](docs/ROADMAP.md) 记录本地个人版的方向建议。SQLite、完整资源采集、独立观看记录和前后端分开部署仍是规划，只有用户另行提出具体任务时才实施，不能把路线图当作自动执行清单。
- 当前目标是本地个人使用，保留现有 Python 与本地启动方式。开发后维护相关契约和交接，使不同 AI 能按同一规范继续工作。

## 交付与 Git 提交

- 用户要求：每次新增需求完成实现并通过必要验证后，自动执行 `git commit`，无需再次询问是否提交。
- 提交信息说明具体变化；提交前检查差异，只纳入本次需求相关文件，保护用户的其他修改。
- 登录凭据、真实入盘记录和运行缓存继续排除在 Git 之外；没有文件变化时说明已有对应提交，不创建空提交。
- 推送按用户授权执行，交付时提供提交号并说明推送结果。

## 环境与用户偏好

- Python 3.10+，零外部依赖，工作目录是本文件所在目录。
- Web：`python web_app.py`，默认 `http://127.0.0.1:8765`；端口占用会顺延，以终端打印地址为准。
- 修改 Python 后必须重启实际提供接口的服务，不能只刷新静态页面。Windows 监听使用独占端口；检查用户实际访问地址的接口，不以新进程打印“已启动”代替验收。旧后台缺少合集字段时显示未知及重启提示；新增/编辑窗口的合集区展示所有字幕组，不受当前字幕组选项隐藏。
- 蜜柑默认代理 `http://127.0.0.1:7897`，可用 `MIKAN_PROXY` 配置。Bangumi 可用 `BANGUMI_PROXY` 配置，默认同蜜柑。
- 百度网盘：`https://pan.baidu.com/disk/main`。优先复用当前 Agent 浏览器内已登录标签页；不同 Agent 的浏览器会话不保证共享。
- 默认保存 `/Anime/<番名>/`，每部番剧一个子目录。绝不误存到根目录，也不删除用户文件。
- 优先简体中文内嵌；“简日内嵌”符合简体要求。资源不满足偏好时保留待确认，不擅自改成繁体或外挂。
- 字幕组按配置顺序优先；同话同组选择最高修订版。已受理、下载中或已入盘的同话不因发现新 hash 而自动重下。
- 新增偏好字段：`subtitle_type`（内嵌/外挂/内封/不限）、`resolution`（2160p/1440p/1080p/720p/480p/未知/不限）、`strict_groups`。启用 strict_groups 时仅用配置中的字幕组；旧配置兼容 embedded_only，默认不限清晰度。准备阶段会停用不符合新偏好的未提交资源，不能因此修改已受理或已完成记录。
- 日常巡检时间：北京时间每天 10:00。巡检未发现可操作变化时保持安静；登录失效、失败、新增提交或入盘变化才通知。
- 用户要求：“每周更新”以 Bangumi 条目的放送星期为准，保留站点星期原值，不按首播日期、字幕资源发布时间或时区换算星期。无 Bangumi 星期的番剧列为待补充；旧官网覆盖资料在下次检查时重新同步 Bangumi，不再优先于 Bangumi。

## 搜索与准备

1. `python agent_cli.py search "番名"` 输出蜜柑、Bangumi 候选。先确认动画名称、年份与条目 ID，不能盲选同名漫画。
2. 确认后使用 `python agent_cli.py watch --mikan-id ID --name "番名" --group "字幕组" --bangumi-id ID`。
3. `python agent_cli.py prepare` 抓取所追番剧并生成队列，**不会提交网盘**。同时保存标题可识别的合集候选，合集不自动入队。网络失败时记录错误，不能据此报告“无新话”。
4. `python agent_cli.py queue` 读取待添加及待核验任务。历史游标 `last_episode` 不代表新目录中已有文件；只有新库里的任务记录和实际网盘证据有效。

合集：新增/编辑追番的字幕组选项包含只有合集的组，版本分别展示单话数和合集数。`data/library.json` 的 collections 保存独立候选，library 导出中可读；详情可复制磁力，显示字幕偏好不符和已执行/待核验话数的重叠提示。标题范围不等于已核验文件清单，范围未知不得从总话数推断。合集不能拆成逐话任务、自动领取或推进入盘进度；实际提交与逐话证据的规则仍需遵守，不能将“发现合集”记为下载成功。

## 在官方网页逐话添加

**用户当前选择**：先完成程序和每天北京时间 10:00 的资源巡检，久留美 4 话保留待添加。当前定时任务仅检查资源和更新 Web，不领取、不提交，也不主动恢复网盘操作；以后用户另行要求恢复添加时，再评估工具能力和已有策略限制。

**当前断点（2026-10-30）**：内置浏览器安全策略拦截了打开百度网盘目录，首次 4 话没有提交。库中 executor=BLOCKED 时不要领取或执行网盘动作；保持资源准备模式。不能重试该被拦截操作，也不能改用其他控制方式、浏览器表面或接口绕过。只有合法工具环境确实发生变化且限制已解除时，才能重新评估正常流程；不能仅为继续任务而清除标记。日常定时任务不要重复报告相同的已知阻碍。

1. 使用你的浏览器/Computer Use 工具打开或复用百度网盘。先检查是否已登录。需要扫码、验证码或安全验证时交给用户，不能捏造成功。
2. 在网盘中打开 `Anime`，检查并创建任务要求的番剧子目录。已有目录直接复用；不要创建带“(1)”后缀的重复目录。
3. **添加前先核验该目录与云添加任务列表**。如果相同话已入盘，不能重复提交；在已有待核验记录上回读结果。若发现 READY 但网盘已有该话，可通过 observe 记录实际完整文件名与路径，source=web；这不声称 Agent 执行过添加。
4. `python agent_cli.py claim --agent "你的Agent名称"` 领取一话。返回的 `claim_token` 只用于本地记录接口，不是百度凭据。一次只领取即将操作的任务。
5. 打开“云添加”，填入该任务磁力，确认保存目录与任务 `save_path` 完全相同。解析磁力、选择文件等操作不等于下载已提交。
6. **在最终点击“确定/开始下载”之前**，通过 report 写入 `SUBMITTING`。这样进程中断后不会自动重复提交。
7. 页面明确受理后立刻写 `SUBMITTED`；任务列表明确显示下载中写 `DOWNLOADING`；明确失败写 `FAILED`；结果不确定写 `UNKNOWN`。每次实际添加都必须马上更新记录，不能等整批结束才补写。
8. 回读该任务与目标目录。只有观察到任务完成/目标文件实际存在且没有 `.bak` 异常时写 `COMPLETED`。`SUBMITTED` 不代表 `COMPLETED`。
9. 继续下一话。每次最多执行 10 话，超出留给下次巡检；准备阶段可保存全部新资源。不要为了核验而反复刷新无变化页面。

report 从 stdin 或 UTF-8 JSON 文件读取：

```json
{"job_id":"领取结果的id","token":"领取结果的claim_token","state":"SUBMITTING","evidence":"已在官网填入该话磁力，保存目录已确认，准备点击最终确定"}
```

调用 `python agent_cli.py report --input report.json`（或默认从 stdin 读取）。Agent 应使用所在环境的安全文件/结构化输入方式，不要把换行 JSON 拼进不可靠的 shell 引号。

已受理任务回读：`python agent_cli.py review --input review.json`，内容为 `job_id`、`state`、`evidence`。仅用于网页实际核验后的状态更新，不能用于伪造已完成。

人工添加或已存在文件：`python agent_cli.py observe --input observation.json`，包含 job_id、state、evidence、source。source=user 只用于用户明确确认的结果，source=web 用于 Agent 亲自观察的网页结果。允许 SUBMITTED / DOWNLOADING / COMPLETED / FAILED，必须有具体证据；领取或提交中的任务不能被覆盖。用户说“已受理”时不能写成 COMPLETED。Web 也提供“记录手动结果”。

Web 详情支持逐话多选和全选后“核实入盘”，确认前列出具体任务。批量 HTTP 入口为 POST `/api/agent/observe-batch`，参数 job_ids、state、source、evidence；仅允许同一番剧和目录，事务内逐话保存来源和证据，任一冲突整批不写入。批量操作同样需要每话真实证据，不能因勾选就视为已入盘。

## 并发、中断与真实性

- 领取租约 15 分钟。未开始提交的领取过期后可重新领取；已进入 SUBMITTING 后中断会成为 UNKNOWN，必须先查看网盘。
- SUBMITTED / DOWNLOADING / UNKNOWN 不允许自动重提；明确失败可通过 Web 重试操作恢复 READY。
- 每条 evidence 写具体观察，包括话数、文件名/任务状态/保存目录；不要写 Cookie、token、完整网络抓包或账号隐私。
- 不修改旧记录来美化结果。失败、未知与 `.bak` 异常都应如实显示。
- 新的搜索匹配、字幕偏好冲突、目标目录冲突等实质不确定信息，先询问用户；常规的已授权目录创建与当前队列提交无需重复确认。

## Web 数据与兼容性

- `data/library.json` 是唯一下载记录库；通过 `catalog.py` / CLI / HTTP 修改，写入已使用文件锁和原子替换。
- `python agent_cli.py library` 可导出；Web 的“操作历史”可查看并导出。每次 report 即刷新 Web 数据。
- Web 的“季度新番”仅浏览 Bangumi 公开资料与评分，可按年份/季度/关键词分页读取 `/api/bangumi/season`。评分人数与缓存时间一起展示；此入口不会新增追番或操作网盘，仍需由用户确认蜜柑条目及字幕偏好后保存。
- HTTP：GET `/api/library`、GET `/api/agent/queue`、POST `/api/agent/claim`、POST `/api/agent/report`、POST `/api/agent/review`，写请求用 JSON。
- 所有常见 Agent 只要支持 Python/本地 HTTP 与浏览器 UI 都可遵循本协议。不能访问同一工作区或浏览器的云端 Agent 不能直接执行本地任务，不应声称兼容即等于自动共享会话。
- 代码改动后运行 `python -m unittest discover -s tests -v`，并按改动检查 `node --check static/app.js` 和 `node --check static/season.js`。测试必须使用临时库和模拟远端，不得往真实网盘提交测试任务；纯文档变更按 DEVELOPMENT.md 做文档验证。
