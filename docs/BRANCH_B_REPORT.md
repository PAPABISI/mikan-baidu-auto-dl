# 分支 B 开发工作报告

> 分支：`B`（基于 `main` @ `43fb39f` 创建）
> 工作副本：`D:/WorkBuddyProject/MiKan_WorkBuddy/mikan-baidu-auto-dl`
> 日期：2026-10-30

## 一、目标

让「蜜柑片架」在**百度网盘连接器没有离线下载接口**的前提下，尽量自动化地完成「手动添加到网盘」之后的收尾工作——即把已经真实入盘的番剧文件自动核验进片库，避免人工在官网逐话肉眼确认、再回 Web 手填证据。

## 二、共完成 4 个提交

| 提交 | 类型 | 内容 |
|---|---|---|
| `18a9832` | chore | 本工作副本默认端口 8765 → 8785，避免与另一个 AI 副本（8765/8766）冲突 |
| `c0549f2` | feat | 网盘连接器清单核验入盘（`verify` 命令 + `POST /api/agent/verify`） |
| `66284d9` | feat | 合集入盘核验——合集覆盖的话数按真实网盘文件建立记录 |
| `7de27d7` | fix | 网盘清单分页截断识别 + 同批次合集沿用，避免进度少算 |

相对 `main` 累计改动 **13 个文件、+836 / −15 行**。

## 三、核心改动说明

### 1. 端口分离（18a9832）
`web_app.py` 默认端口 `8765 → 8785`，`start.bat` 无写死端口、自动跟随。同步更新 README / AGENTS / DEVELOPMENT。验收：8785 首页 200、返回本副本片库数据。

### 2. 单话核验（c0549f2）
新增模块 `verify.py`，核心接口 `verify_listing(save_path, files, source, apply, collection_id, complete)`：

- 用网盘目录文件清单，按**话数唯一匹配**本地任务，`READY / SUBMITTED / DOWNLOADING / UNKNOWN / FAILED` 状态可核验
- `CLAIMED / SUBMITTING / SUPERSEDED / COMPLETED` 绝不覆盖
- 同一话多文件、或多候选任务 → 只报歧义，不写入
- 证据自动生成，含真实文件名 + 大小，来源标 `web`（连接器只读观察）或 `user`
- 接入 `agent_cli.py verify` 子命令与 `POST /api/agent/verify` 路由

### 3. 合集入盘核验（66284d9）
解决「合集只存候选、不建逐话任务 → 详情页无可勾选任务、无法核实」的问题：

- 网盘里真实存在、该话无任务、且被合集候选覆盖 → 按真实文件建立 `origin=collection` 的 COMPLETED 记录（`catalog._record_collection_episode`）
- 多合集覆盖同一话必须显式指定 `--collection`，否则返回 `collection_choices` 不写入
- 合集范围未入盘话数在 `collection_missing_episodes` 报告，不虚构进度
- 新增 `agent_cli.py collection-observe`、`POST /api/collections/observe`、Web 合集卡「记录该合集入盘」按钮

### 4. 分页截断 + 同批次沿用（7de27d7）
- 连接器目录列表**每页最多 10 项**，单页清单会把后续页文件误判为「未入盘」（漂流少年显示 10/12）。新增 `--input` 多页合并、`--complete` 声明取全；条目数正好是每页上限且未声明完整时只警告 `suspect_truncated`、缺失话数标 `certain=false`
- `_normalize` 按文件名+大小去重，分页重叠不再被当成「同话多文件」歧义
- 同目录已入盘记录只来自一个合集时，剩余话数自动沿用（`selected_by=same_batch`）；已用过多个合集仍要求显式指定

## 四、验证与真实数据

- 测试：**146 项全部通过**（原 119 项 + 新增单测/HTTP 路由测试）
- 前端 `static/app.js` 通过 `node --check`
- 真实端到端：
  - 久留美 01–04 → COMPLETED
  - 漂流少年 01–12 合集 → **12/12 COMPLETED**（含分页补齐的 05/06）
  - 当前片库：COMPLETED 16、READY 8（青之箱 01–04、学生会 01–04）

## 五、尚未完成 / 边界

- **云添加（磁力提交到网盘）**：连接器无离线下载接口，这一步仍需在官网网页版手动完成；之后核验可一句话自动化。
- 数据分叉：`data/library.json` 不入 Git，两个 AI 副本各自独立空库，互不影响。
- 追番列表：用户经 Web 界面新增的「漂流少年」配置已随 66284d9 提交入库。

## 六、后续建议

浏览器自动化（Chrome + Playwright）已验证可驱动网页版「云添加」弹窗，可考虑新增 `browser_exec.py` 把「磁力提交」也做成半自动化（需用户确认），形成完整闭环。
