# 项目入口

先阅读本目录 `AGENTS.md`。其中的浏览器操作协议、CLI、目录规则、偏好与逐次记录要求同样适用于 Claude Code、OpenClaw 等其他 Agent。

开发前继续阅读 `DEVELOPMENT.md`，并查看 `README.md` 和 `HANDOFF.md` 顶部。`docs/ROADMAP.md` 是方向规划，不能自动实施尚未授权的数据库迁移、采集扩展或前后端拆分。完成新增需求后按统一规范验证并自动 commit。

生产入口：`agent_cli.py`，Web 入口：`web_app.py`。不要运行旧的扫码/直接提交 API 入口来替代当前的官方网页方案。
