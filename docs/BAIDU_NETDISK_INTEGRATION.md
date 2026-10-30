# 百度网盘连接器接口说明（给协作 AI）

> 本文说明我们在「蜜柑片架」里如何使用百度网盘连接器（`baidu-netdisk`），供并行开发的另一个 AI 对齐能力边界与调用约定。

## 一、结论先行

1. **连接器没有离线下载 / 磁力 / BT 种子提交接口**。往网盘提交磁力（云添加）只能走**网页版 UI**，连接器干不了这件事。
2. 连接器能做的：**目录浏览、关键词/语义检索、文件元信息、容量查询、新建文件夹、移动/复制/重命名、分享链接、文本保存、URL 转存**。
3. 连接器**不能删除**网盘文件。
4. 我们在项目里只把连接器当作**只读核验来源**：读目录 → 匹配任务 → 写 `COMPLETED` 证据，从不通过连接器写网盘。

## 二、工具清单与用途

| 工具 | 用途 |
|---|---|
| `user_info` | 查账号信息（昵称、会员、容量） |
| `get_quota` | 查剩余空间 |
| `file_list` | 列目录（**见下方分页坑**） |
| `file_meta` | 查单个文件/目录元信息 |
| `file_keyword_search` / `file_semantics_search` | 关键词/语义检索 |
| `make_dir` | 新建目录 |
| `file_move` / `file_copy` / `file_rename` | 移动 / 复制 / 重命名 |
| `file_sharelink_set` 等 | 分享链接 |
| `file_upload` / `file_upload_by_url` / `file_upload_by_content` | 上传 / URL 转存（**只接受可直接下载的 HTTP 链接，磁力不行**） |

## 三、关键坑（务必遵守）

### 1. `file_list` 每页最多 10 项，必须翻页取全
- 实测：`file_list` 返回的 `data.list` **每页最多 10 条**。
- 必须用 `page=1, 2, 3…` 翻页，直到某一页返回不足 10 条（或空）为止。
- **绝不能拿单页结果断言「某文件不存在 / 未入盘」**——漂流少年就因此误判成 10/12。

### 2. 目录路径需要 URL 编码
- 带中文、空格的目录（如 `/Anime/青之箱 第二季`），传 `dir` 前先 URL 编码（空格编码为 `%20`）。
- 错误示范：直接传 `/Anime/漂流少年` 可能返回空或异常。

### 3. 不能删除
- 没有删除工具。任何「试一下转存」的操作都要慎重，因为留下的文件只能手动清理。

## 四、在项目里的接入方式

核验入盘统一走 `verify.py` 的 `verify_listing(save_path, files, source, apply, collection_id, complete)`：

```
POST /api/agent/verify
body: {
  "save_path": "/Anime/番名",
  "files": [{"filename": "...", "size": 453786047}],  # 来自 file_list 各页合并、去重后的清单
  "source": "web",        # 连接器只读观察=web；用户确认=user
  "complete": true,       # 已取全各页才为 true
  "collection": "可选合集编号或 infohash"
}
```

- `files` 由**调用方（Agent）的只读连接器工具**提供，本项目代码**不访问网盘、不存凭据**。
- 匹配规则保守：话数唯一才写；歧义只报；执行中/已入盘/已替换不改写。
- 合集：无任务但被合集覆盖的真实文件建 `origin=collection` 记录；多合集覆盖同一话自动沿用同目录唯一已用合集（`same_batch`），否则需显式指定。

## 五、边界与禁忌

- 连接器凭据 / 登录态是敏感数据，**不得写入版本库**（`baidu_keys.json`、`.baidu_cookies.json`、浏览器 profile 等均已在 `.gitignore`）。
- 不要用连接器做任何写网盘（移动/复制/重命名/转存）的「试探」，除非用户明确要求。
- 云添加（磁力提交）请继续引导用户到官网网页版手动完成，或另走浏览器自动化（另一个 AI 的验证结论，见 `HANDOFF.md`）。
