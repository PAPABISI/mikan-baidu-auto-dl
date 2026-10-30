library.json 为真实追番库、下载队列和操作历史，不含百度登录凭据。
文件不进入 Git；请按需备份，Web 的“导出记录”和 agent_cli.py library 可导出不含凭据的数据。
每次修改均使用文件锁和原子写入。请通过 CLI 或本地 HTTP API 修改，不要直接编辑正在使用的数据文件。
