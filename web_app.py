# -*- coding: utf-8 -*-
"""本地 Web 控制台：python web_app.py [--open] [--port 8765]。"""
import argparse
import collections
import copy
import datetime as dt
import html
import json
import mimetypes
import os
import re
import socket
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import auto_dl
import agent_bridge
import catalog
import metadata
import season
import storage
from baidu import BaiduClient, LoginRequired, NETWORK_ERRORS, make_opener, req, safe_error
from parse_mikan import parse_mikan, subtitle_kind
from qr_cloud_dl import ensure_login

BASE = storage.BASE
STATIC_DIR = os.path.join(BASE, "static")
QR_PNG = os.path.join(STATIC_DIR, "qr.png")
QR_STATE_FILE = os.path.join(BASE, ".qr_state.json")
HOST, PORT_DEFAULT = "127.0.0.1", 8765
_qr_lock = threading.Lock()
_qr_state = {"state": "IDLE", "message": "", "qr_url": "", "expires_at": "", "started_at": ""}
_refresh_lock = threading.Lock()
_refresh_running = False
_login_lock = threading.Lock()
_login_cache = {}
_login_checked = 0.0


def _qr_set(**changes):
    with _qr_lock:
        if changes.pop("qr_ready", False):
            changes["qr_url"] = f"/static/qr.png?t={time.time_ns()}"
        _qr_state.update(changes)
        storage.write_json(QR_STATE_FILE, _qr_state)


def _qr_get():
    with _qr_lock:
        return copy.deepcopy(_qr_state)


def login_status(force=False):
    global _login_checked, _login_cache
    with _login_lock:
        if not force and _login_cache and time.monotonic() - _login_checked < 60:
            return dict(_login_cache)
        try:
            state = BaiduClient().login_info()
        except LoginRequired as error:
            state = {"logged_in": False, "status": "QRCODE_NEEDED", "msg": str(error)}
        except NETWORK_ERRORS as error:
            state = {"logged_in": None, "status": "NET_ERROR", "msg": safe_error(error)}
        except Exception as error:
            state = {"logged_in": None, "status": "GENERIC", "msg": safe_error(error)}
        state["checked_at"] = dt.datetime.now().isoformat(timespec="seconds")
        _login_checked = time.monotonic()
        _login_cache = state
        return dict(state)


def _login_monitor(stop):
    while not stop.is_set():
        if _qr_get()["state"] not in ("WAITING", "LOGGED_IN"):
            login_status(force=True)
        stop.wait(300)


def _qr_worker(force=False):
    global _login_cache, _login_checked
    try:
        client = ensure_login(qr_path=QR_PNG, on_state=_qr_set, force=force)
        with _login_lock:
            _login_cache = {**client.login_profile, "checked_at": dt.datetime.now().isoformat(timespec="seconds")}
            _login_checked = time.monotonic()
        _qr_set(state="DONE", message=client.login_profile.get("warning") or "登录成功，可点击立即检查处理追番列表",
                qr_url="", expires_at="")
    except Exception as error:
        _qr_set(state="FAILED", message=safe_error(error), qr_url="")
    finally:
        try:
            os.remove(QR_PNG)
        except FileNotFoundError:
            pass


def read_state():
    state = storage.read_json(auto_dl.RUN_LOG, {"status": "NEVER", "msg": "尚未检查过"})
    with _refresh_lock:
        if _refresh_running and state.get("status") != "RUNNING":
            return {"status": "RUNNING", "msg": "正在检查追番列表", "submitted": []}
    # 上次进程被终止后不能永远显示运行中；持锁进程仍在则保留运行状态。
    if state.get("status") == "RUNNING":
        try:
            with auto_dl.FileLock(agent_bridge.PREPARE_LOCK, timeout=0):
                if not _refresh_running:
                    state.update(status="INTERRUPTED", msg="上次检查已中断；提交记录仍保留，可重新检查")
        except TimeoutError:
            pass
    return state


def _refresh_worker():
    global _refresh_running
    try:
        agent_bridge.prepare()
    finally:
        with _refresh_lock:
            _refresh_running = False


def start_refresh():
    global _refresh_running
    with _refresh_lock:
        if _refresh_running:
            return False
        _refresh_running = True
    threading.Thread(target=_refresh_worker, daemon=True).start()
    return True


def fetch_mikan(url):
    opener, _ = make_opener(proxy=auto_dl.PROXY_WEB)
    return req(opener, url, referer="https://mikanani.me/", timeout=30)


def mikan_search(query):
    text = fetch_mikan("https://mikanani.me/Home/Search?" +
                       urllib.parse.urlencode({"searchstr": query})).decode("utf-8", "replace")
    pattern = re.compile(
        r'href=["\']/Home/Bangumi/(\d+)["\'][^>]*>.*?data-src=["\']([^"\']+)["\'][^>]*>.*?'
        r'<div class=["\']an-text["\'][^>]*title=["\']([^"\']*)["\']', re.S)
    results, seen = [], set()
    for match in pattern.finditer(text):
        bid, cover, name = match.groups()
        if bid not in seen:
            seen.add(bid)
            results.append({"id": int(bid), "name": html.unescape(name),
                            "cover": urllib.parse.urljoin("https://mikanani.me", html.unescape(cover))})
    return results


def mikan_bangumi(bid):
    items = parse_mikan(auto_dl.fetch_mikan(bid))
    if not items:
        raise ValueError("未解析到资源，请检查蜜柑页面或代理")
    groups = collections.defaultdict(set)
    batches = collections.defaultdict(list)
    samples = {}
    for item in items:
        kind = subtitle_kind(item)
        if item["ep"] != "?" or item.get("is_collection"):
            key = (item["group"], item["lang"], kind, item["resolution"])
            if item.get("is_collection"):
                groups[key]  # 只有合集的字幕组也需要出现在选择列表中。
                batches[key].append({**item, "subtitle_type": kind})
            else:
                groups[key].add(item["ep"])
            samples.setdefault(key, item["title"])
    return [{"group": group, "language": lang, "type": kind, "resolution": resolution,
             "count": len(eps), "episodes": sorted(eps, key=float), "sample": samples[(group, lang, kind, resolution)],
             "collection_count": len(batches[(group, lang, kind, resolution)]), "collections": batches[(group, lang, kind, resolution)]}
            for (group, lang, kind, resolution), eps in sorted(groups.items())]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > 1048576:
            raise ValueError("请求体超过限制")
        if not length:
            return {}
        if self.headers.get_content_type() != "application/json":
            raise ValueError("请使用 application/json")
        body = json.loads(self.rfile.read(length))
        if not isinstance(body, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return body

    def _handle(self, method):
        try:
            port = self.server.server_port
            allowed = {f"localhost:{port}", f"127.0.0.1:{port}"}
            if self.headers.get("Host", "") not in allowed:
                return self._send(403, {"error": "仅允许本机访问"})
            origin = self.headers.get("Origin")
            if origin and origin not in {f"http://{host}" for host in allowed}:
                return self._send(403, {"error": "不允许跨站请求"})
            self._route(method)
        except (ValueError, TypeError, KeyError) as error:
            self._send(400, {"error": safe_error(error)})
        except IndexError:
            self._send(404, {"error": "追番项不存在，请刷新列表"})
        except LoginRequired as error:
            self._send(401, {"error": str(error), "status": "QRCODE_NEEDED"})
        except NETWORK_ERRORS as error:
            self._send(502, {"error": safe_error(error), "status": "NET_ERROR"})
        except TimeoutError as error:
            self._send(409, {"error": safe_error(error)})
        except Exception as error:
            self._send(500, {"error": safe_error(error)})

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")

    def _route(self, method):
        url = urllib.parse.urlsplit(self.path)
        path, query = url.path, urllib.parse.parse_qs(url.query)
        body = self._read_body() if method in ("POST", "PUT", "DELETE") else {}
        if method == "GET":
            if path in ("/", "/index.html"):
                return self._serve_static("index.html")
            if path.startswith("/static/"):
                return self._serve_static(urllib.parse.unquote(path[8:]))
            if path == "/api/series":
                return self._send(200, storage.load_list())
            if path in ("/api/library", "/api/library/export"):
                return self._send(200, catalog.snapshot(include_all_events=path.endswith("/export")))
            if path == "/api/agent/queue":
                return self._send(200, {"jobs": catalog.snapshot()["jobs"]})
            if path == "/api/bangumi/search":
                term = query.get("q", [""])[0].strip()
                if not term:
                    raise ValueError("请输入番名")
                return self._send(200, {"results": metadata.search_bangumi(term)})
            if path == "/api/bangumi/season":
                year, quarter = season.current_season()
                return self._send(200, season.browse(
                    year=int(query.get("year", [str(year)])[0]), quarter=int(query.get("quarter", [str(quarter)])[0]),
                    page=int(query.get("page", ["1"])[0]), sort=query.get("sort", ["score"])[0],
                    query=query.get("q", [""])[0], refresh=query.get("refresh") == ["1"]))
            if path == "/api/agent/guide":
                with open(os.path.join(BASE, "AGENTS.md"), encoding="utf-8") as stream:
                    return self._send(200, stream.read(), "text/plain; charset=utf-8")
            if path == "/api/tasks":
                mikan_id = int(query.get("mikan_id", ["0"])[0])
                history = storage.read_json(auto_dl.HISTORY_FILE, {})
                tasks = [{**record, "key": key} for key, record in history.items()
                         if record["mikan_id"] == mikan_id]
                return self._send(200, {"tasks": tasks})
            if path == "/api/state":
                return self._send(200, read_state())
            if path == "/api/cookies":
                return self._send(200, login_status(force=query.get("force") == ["1"]))
            if path == "/api/qr/status":
                return self._send(200, _qr_get())
            if path == "/api/mikan/search":
                term = query.get("q", [""])[0].strip()
                if not term:
                    raise ValueError("请输入番名")
                return self._send(200, {"results": mikan_search(term)})
            if re.fullmatch(r"/api/mikan/bangumi/[1-9]\d*", path):
                return self._send(200, {"groups": mikan_bangumi(int(path.rsplit("/", 1)[-1])), "collection_support": True})
            if path == "/api/mikan/cover":
                target = urllib.parse.urlsplit(query.get("url", [""])[0])
                if target.scheme != "https" or target.netloc != "mikanani.me" or not target.path.startswith("/images/"):
                    raise ValueError("不支持的封面地址")
                return self._send(200, fetch_mikan(target.geturl()), "image/jpeg")
            if path == "/api/cover":
                target = urllib.parse.urlsplit(query.get("url", [""])[0])
                valid_mikan = target.netloc == "mikanani.me" and target.path.startswith("/images/")
                valid_bgm = target.netloc == "lain.bgm.tv" and target.path.startswith(("/pic/cover/", "/r/"))
                if target.scheme != "https" or not (valid_mikan or valid_bgm):
                    raise ValueError("不支持的封面地址")
                return self._send(200, fetch_mikan(target.geturl()), mimetypes.guess_type(target.path)[0] or "image/jpeg")
            if path == "/api/pan/ls":
                client = BaiduClient()
                client.login_info()
                return self._send(200, {"errno": 0, "files": client.list_files(query.get("path", ["/"])[0])})
        if method == "POST":
            if path == "/api/series":
                return self._add_series(body)
            if path in ("/api/refresh", "/api/agent/prepare"):
                started = start_refresh()
                return self._send(202 if started else 409,
                                  {"status": "RUNNING" if started else "BUSY", "msg": "正在检查追番列表"})
            if path == "/api/agent/claim":
                return self._send(200, {"job": catalog.claim(body.get("agent"), body.get("job_id"))})
            if path == "/api/agent/report":
                return self._send(200, {"job": catalog.report(body["job_id"], body["token"], body["state"], body["evidence"], body.get("remote_task_id"))})
            if path == "/api/agent/review":
                return self._send(200, {"job": catalog.review(body["job_id"], body["state"], body["evidence"])})
            if path == "/api/agent/observe":
                return self._send(200, {"job": catalog.observe(body["job_id"], body["state"], body["evidence"], body["source"])})
            if path == "/api/agent/observe-batch":
                return self._send(200, {"jobs": catalog.observe_batch(body["job_ids"], body["state"], body["evidence"], body["source"])})
            if path == "/api/agent/retry":
                return self._send(200, {"job": catalog.retry(body["job_id"], body["evidence"])})
            if re.fullmatch(r"/api/metadata/[1-9]\d*", path):
                sid = int(path.rsplit("/", 1)[-1])
                values = metadata.get_subject(body["bangumi_id"]) if "bangumi_id" in body else body
                return self._send(200, {"metadata": catalog.save_metadata(sid, values)})
            if path == "/api/tasks/retry":
                mikan_id = body.get("mikan_id")
                if type(mikan_id) is not int or mikan_id <= 0:
                    raise ValueError("mikan_id 必须是正整数")
                count = auto_dl.retry_failed(mikan_id)
                return self._send(200, {"ok": True, "msg": f"已重置 {count} 项失败记录"})
            if path == "/api/tasks/resolve":
                auto_dl.resolve_unknown(body.get("key"), body.get("action"))
                return self._send(200, {"ok": True})
            if path in ("/api/scan-qr", "/api/qr/start"):
                force = body.get("force", False)
                if type(force) is not bool:
                    raise ValueError("force 必须是布尔值")
                with _qr_lock:
                    if _qr_state["state"] in ("WAITING", "LOGGED_IN"):
                        return self._send(409, {"error": "已有登录流程，请等待完成"})
                    _qr_state.update(state="WAITING", message="正在检查登录态...", qr_url="",
                                     expires_at="", started_at=dt.datetime.now().isoformat())
                threading.Thread(target=_qr_worker, args=(force,), daemon=True).start()
                return self._send(202, {"ok": True, "msg": "登录流程已启动"})
        if method in ("PUT", "DELETE") and re.fullmatch(r"/api/series/\d+", path):
            index = int(path.rsplit("/", 1)[-1])
            def change(cfg):
                item = cfg["series"][index]
                # 前端附上 mikan_id，避免列表已变动时误操作另一个索引。
                if "mikan_id" in body and body["mikan_id"] != item["mikan_id"]:
                    raise ValueError("列表已变更，请刷新后重试")
                if method == "DELETE":
                    cfg["series"].pop(index)
                    return {"ok": True, "removed": item["name"]}
                for key in ("subtitle_groups", "language", "embedded_only", "subtitle_type", "resolution", "strict_groups", "save_path", "notes", "cover_url"):
                    if key in body:
                        item[key] = body[key]
                if "subtitle_type" in body:
                    item["embedded_only"] = body["subtitle_type"] == "内嵌"
                elif "embedded_only" in body:
                    item["subtitle_type"] = "内嵌" if body["embedded_only"] else "不限"
                storage.validate_series(item)
                catalog.check_target(item["save_path"])
                return {"ok": True, "series": item}
            return self._send(200, storage.mutate_list(change))
        self._send(404, {"error": "未找到接口"})

    def _add_series(self, body):
        name = body.get("name")
        item = {
            "mikan_id": body.get("mikan_id"), "name": name,
            "subtitle_groups": body.get("subtitle_groups", []),
            "language": body.get("language", "简体"), "embedded_only": body.get("embedded_only", True),
            "save_path": body.get("save_path", catalog.target_path(name or "")), "last_episode": "0",
            "last_check": None, "last_check_status": None, "last_check_detail": None,
            "cover_url": body.get("cover_url"), "notes": body.get("notes", ""),
            "added_at": dt.date.today().isoformat(),
        }
        for key in ("subtitle_type", "resolution", "strict_groups"):
            if key in body:
                item[key] = body[key]
        if "subtitle_type" in body:
            item["embedded_only"] = body["subtitle_type"] == "内嵌"
        storage.validate_series(item)
        catalog.check_target(item["save_path"])
        def add(cfg):
            if any(s["mikan_id"] == item["mikan_id"] for s in cfg["series"]):
                raise ValueError("这部番已经在追番列表中")
            cfg["series"].append(item)
            return {"ok": True, "series": item}
        self._send(201, storage.mutate_list(add))

    def _serve_static(self, name):
        root = os.path.realpath(STATIC_DIR)
        path = os.path.realpath(os.path.join(root, name))
        if os.path.commonpath([root, path]) != root or not os.path.isfile(path):
            return self._send(404, {"error": "文件不存在"})
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/"):
            ctype += "; charset=utf-8"
        with open(path, "rb") as stream:
            self._send(200, stream.read(), ctype)


class LocalHTTPServer(ThreadingHTTPServer):
    # Windows 的 SO_REUSEADDR 会让新旧进程同时占用同一端口，响应可能来自旧服务。
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def create_server(port=PORT_DEFAULT):
    for candidate in range(port, port + 10):
        try:
            return LocalHTTPServer((HOST, candidate), Handler)
        except OSError as error:
            if error.errno not in (48, 98, 10048):
                raise
    raise OSError("控制台端口均被占用，请使用 --port 指定端口")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--open", action="store_true", help="启动后打开浏览器")
    parser.add_argument("--port", type=int, default=PORT_DEFAULT)
    args = parser.parse_args()
    if not 1 <= args.port <= 65526:
        parser.error("端口必须在 1 到 65526 之间")
    server = create_server(args.port)
    stop = threading.Event()
    # 浏览器方案由 Agent 在官网确认登录，不轮询旧的 Cookie 接口。
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"Web 控制台已启动: {url}\n按 Ctrl+C 退出", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n关闭中...")
    finally:
        stop.set()
        server.server_close()


if __name__ == "__main__":
    main()
