"""跨 Agent 命令行协议。所有命令输出 UTF-8 JSON；report 支持从 stdin 读取 JSON。"""
import argparse
import contextlib
import io
import json
import sys

import agent_bridge
import catalog
import metadata
from baidu import safe_error


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    search = commands.add_parser("search", help="搜索蜜柑和 Bangumi 候选")
    search.add_argument("query")
    search.add_argument("--source", choices=("mikan", "bangumi", "all"), default="all")
    watch = commands.add_parser("watch", help="确认追番对象与目标目录")
    watch.add_argument("--mikan-id", type=int, required=True)
    watch.add_argument("--name", required=True)
    watch.add_argument("--group", action="append", default=[])
    watch.add_argument("--bangumi-id", type=int)
    watch.add_argument("--path")
    watch.add_argument("--language", choices=("简体", "繁体", "简繁", "不限"))
    watch.add_argument("--subtitle-type", choices=("内嵌", "外挂", "内封", "不限"))
    watch.add_argument("--resolution", choices=("不限", "2160p", "1440p", "1080p", "720p", "480p", "未知"))
    watch.add_argument("--only-group", action="store_true")
    prepare = commands.add_parser("prepare", help="抓取并生成待添加清单，不操作网盘")
    prepare.add_argument("--mikan-id", type=int)
    queue = commands.add_parser("queue", help="读取队列；默认返回所有需执行或核验的任务")
    queue.add_argument("--state", action="append")
    claim = commands.add_parser("claim", help="领取一话，避免多个 Agent 同时提交")
    claim.add_argument("--agent", required=True)
    claim.add_argument("--job-id")
    report = commands.add_parser("report", help="逐次回报网页观察结果")
    report.add_argument("--input", default="-", help="JSON 文件路径，或 - 表示 stdin")
    review = commands.add_parser("review", help="回读网盘后更新已受理/下载中/未知任务")
    review.add_argument("--input", default="-")
    observe = commands.add_parser("observe", help="记录用户确认或网页可见的手动添加/已存在结果")
    observe.add_argument("--input", default="-")
    commands.add_parser("library", help="导出不含凭据的 Web 数据")
    args = parser.parse_args(argv)
    try:
        if args.command == "search":
            result = {}
            if args.source in ("mikan", "all"):
                from web_app import mikan_search
                try:
                    result["mikan"] = mikan_search(args.query)
                except Exception as error:
                    result["mikan_error"] = safe_error(error)
            if args.source in ("bangumi", "all"):
                try:
                    result["bangumi"] = metadata.search_bangumi(args.query)
                except Exception as error:
                    result["bangumi_error"] = safe_error(error)
        elif args.command == "watch":
            fields = {"save_path": args.path} if args.path else {}
            for field in ("language", "subtitle_type", "resolution"):
                if getattr(args, field) is not None:
                    fields[field] = getattr(args, field)
            if args.only_group:
                fields["strict_groups"] = True
            result = catalog.watch_series(args.mikan_id, args.name, args.group, **fields)
            if args.bangumi_id:
                catalog.save_metadata(args.mikan_id, metadata.get_subject(args.bangumi_id))
        elif args.command == "prepare":
            with contextlib.redirect_stdout(io.StringIO()):
                result = agent_bridge.prepare(args.mikan_id)
        elif args.command == "queue":
            states = set(args.state or ["READY", "CLAIMED", "SUBMITTING", "SUBMITTED", "DOWNLOADING", "UNKNOWN"])
            if not states <= catalog.ALL_STATES:
                raise ValueError("未知任务状态")
            result = [job for job in catalog.snapshot()["jobs"] if job["state"] in states]
        elif args.command == "claim":
            result = catalog.claim(args.agent, args.job_id)
        elif args.command in ("report", "review", "observe"):
            if args.input == "-":
                body = json.load(sys.stdin)
            else:
                with open(args.input, encoding="utf-8-sig") as stream:
                    body = json.load(stream)
            if not isinstance(body, dict):
                raise ValueError("输入必须是 JSON 对象")
            if args.command == "report":
                result = catalog.report(body["job_id"], body["token"], body["state"], body["evidence"], body.get("remote_task_id"))
            elif args.command == "review":
                result = catalog.review(body["job_id"], body["state"], body["evidence"])
            else:
                result = catalog.observe(body["job_id"], body["state"], body["evidence"], body["source"])
        else:
            result = catalog.snapshot()
        print(json.dumps({"ok": True, "data": result}, ensure_ascii=False, indent=2))
        return 2 if args.command == "prepare" and result.get("status") in ("GENERIC", "BUSY") else 0
    except Exception as error:
        print(json.dumps({"ok": False, "error": safe_error(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
