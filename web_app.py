# -*- coding: utf-8 -*-
"""
Web 控制台后端（Python 标准库实现，零外部依赖）。
启动: python web_app.py
默认监听 http://localhost:8765
"""
import datetime as dt
import http.cookiejar
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from filelock import FileLock

BASE = os.path.dirname(os.path.abspath(__file__))
LIST_FILE = os.path.join(BASE, "追番列表.json")
LIST_LOCK = os.path.join(BASE, ".list.lock")
COOKIE_FILE = os.path.join(BASE, ".baidu_cookies.json")
RUN_LOG = os.path.join(BASE, ".auto_dl_state.json")
STATIC_DIR = os.path.join(BASE, "static")

PROXY_WEB = "http://127.0.0.1:7897"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
PY = sys.executable
HOST, PORT_DEFAULT = "127.0.0.1", 8765


# ============ 工具函数 ============
def fetch(url, data=None, proxy=None, timeout=20, referer=None):
    """通用 HTTP 抓取。"""
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        handlers.append(urllib.request.ProxyHandler({}))
    op = urllib.request.build_opener(*handlers)
    r = urllib.request.Request(url, data=data)
    r.add_header("User-Agent", UA)
    r.add_header("Accept", "*/*")
    if referer:
        r.add_header("Referer", referer)
    with op.open(r, timeout=timeout) as resp:
        return resp.read()


def baidu_opener_from_cookies():
    if not os.path.exists(COOKIE_FILE):
        return None, None
    cookies = json.load(open(COOKIE_FILE, encoding="utf-8"))
    jar = http.cookiejar.CookieJar()
    for k, v in cookies.items():
        ck = http.cookiejar.Cookie(0, k, v, None, False, ".baidu.com", True, True,
                                   "/", True, False, None, False, None, None, {}, False)
        jar.set_cookie(ck)
    op = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(jar))
    return op, cookies


def load_list():
    if not os.path.exists(LIST_FILE):
        return {"series": []}
    return json.load(open(LIST_FILE, encoding="utf-8"))


def save_list(cfg):
    with FileLock(LIST_LOCK, timeout=15):
        json.dump(cfg, open(LIST_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


# ============ 蜜柑 ============
def mikan_search(q):
    """代理蜜柑搜索，返回 [{id, name, cover}]。"""
    qenc = urllib.parse.quote(q)
    url = f"https://mikanani.me/Home/Search?searchstr={qenc}"
    html_txt = fetch(url, proxy=PROXY_WEB, referer="https://mikanani.me/",
                     timeout=30).decode("utf-8", "replace")
    results = []
    # 每个 Bangumi 卡片结构：<a href="/Home/Bangumi/{id}"> ... <span data-src="...jpg" ...>
    # ... <div class="an-text" title="番名">
    pat = re.compile(
        r'href="/Home/Bangumi/(\d+)"[^>]*>.*?data-src="([^"]+)"[^>]*>.*?'
        r'<div class="an-text"[^>]*title="([^"]*)"',
        re.S)
    seen = set()
    for m in pat.finditer(html_txt):
        bid, cover, name = m.group(1), m.group(2), m.group(3)
        if bid in seen:
            continue
        seen.add(bid)
        # HTML 实体解码
        import html as htmllib
        name = htmllib.unescape(name)
        if not cover.startswith("http"):
            cover = "https://mikanani.me" + cover
        results.append({"id": int(bid), "name": name, "cover": cover})
    return results


def mikan_bangumi(bangumi_id):
    """解析详情页，返回字幕组/语言分布。"""
    from parse_mikan import parse_mikan
    url = f"https://mikanani.me/Home/Bangumi/{bangumi_id}"
    html_txt = fetch(url, proxy=PROXY_WEB, referer="https://mikanani.me/",
                     timeout=30).decode("utf-8", "replace")
    items = parse_mikan(html_txt)
    # 聚合：group × lang × 嵌入/外挂 × 话数集合
    import collections
    agg = collections.defaultdict(lambda: {"episodes": set(), "embedded": 0, "soft": 0})
    for it in items:
        key = (it["group"], it.get("lang", "未知"), "内嵌" if it["embedded"] else "外挂")
        agg[key]["episodes"].add(it["ep"])
        if it["embedded"]:
            agg[key]["embedded"] += 1
        else:
            agg[key]["soft"] += 1
    out = []
    for (grp, lang, kind), v in sorted(agg.items()):
        eps = sorted(v["episodes"], key=lambda x: (len(x), x))
        out.append({
            "group": grp, "language": lang, "type": kind,
            "episodes": eps, "count": len(eps),
        })
    return out


# ============ 网盘 ============
def pan_list(path):
    """用 BDUSS 调 web API 列目录。"""
    op, _ = baidu_opener_from_cookies()
    if not op:
        return {"errno": -1, "errmsg": "未登录"}
    url = (f"https://pan.baidu.com/api/list?order=name&desc=0"
           f"&clienttype=0&app_id=250528&web=1&dir={urllib.parse.quote(path)}")
    try:
        raw = fetch_url_with_opener(op, url, referer="https://pan.baidu.com/disk/main",
                                     timeout=20)
        d = json.loads(raw)
        if d.get("errno") != 0:
            return {"errno": d.get("errno"), "errmsg": d.get("show_msg", ""),
                    "raw": d}
        return {"errno": 0,
                "files": [{"name": f.get("server_filename") or f.get("filename"),
                           "size": f.get("size"), "isdir": f.get("isdir", 0),
                           "fs_id": f.get("fs_id")} for f in d.get("list", [])]}
    except Exception as e:
        return {"errno": -2, "errmsg": str(e)}


def fetch_url_with_opener(op, url, referer=None, timeout=20):
    r = urllib.request.Request(url)
    r.add_header("User-Agent", UA)
    if referer:
        r.add_header("Referer", referer)
    with op.open(r, timeout=timeout) as resp:
        return resp.read()


# ============ HTTP Handler ============
class Handler(BaseHTTPRequestHandler):
    # 静默默认日志
    def log_message(self, fmt, *args):
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
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        ln = int(self.headers.get("Content-Length", "0"))
        if not ln:
            return {}
        raw = self.rfile.read(ln)
        try:
            return json.loads(raw)
        except Exception:
            return dict(urllib.parse.parse_qsl(raw.decode("utf-8", "replace")))

    # ---------- GET ----------
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        p = u.path
        if p == "/" or p == "/index.html":
            return self._serve_static("index.html", "text/html; charset=utf-8")
        if p.startswith("/static/"):
            return self._serve_static(p[8:], None)
        if p == "/api/series":
            return self._send(200, {"series": load_list().get("series", [])})
        if p == "/api/state":
            return self._send(200, self._read_state())
        if p == "/api/cookies":
            return self._send(200, {"logged_in": os.path.exists(COOKIE_FILE)})
        if p.startswith("/api/mikan/search"):
            q = urllib.parse.parse_qs(u.query).get("q", [""])[0]
            if not q.strip():
                return self._send(400, {"error": "缺 q 参数"})
            try:
                return self._send(200, {"results": mikan_search(q)})
            except Exception as e:
                return self._send(500, {"error": str(e)})
        if p.startswith("/api/mikan/bangumi/"):
            bid = p.rsplit("/", 1)[-1]
            try:
                return self._send(200, {"groups": mikan_bangumi(bid)})
            except Exception as e:
                return self._send(500, {"error": str(e)})
        if p.startswith("/api/pan/ls"):
            path = urllib.parse.parse_qs(u.query).get("path", [""])[0]
            return self._send(200, pan_list(path))
        return self._send(404, {"error": "Not Found", "path": p})

    # ---------- POST ----------
    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        p = u.path
        body = self._read_body()
        if p == "/api/series":
            return self._add_series(body)
        if p == "/api/refresh":
            return self._refresh()
        if p == "/api/scan-qr":
            return self._scan_qr()
        return self._send(404, {"error": "Not Found"})

    # ---------- PUT ----------
    def do_PUT(self):
        u = urllib.parse.urlparse(self.path)
        p = u.path
        body = self._read_body()
        if p.startswith("/api/series/"):
            idx = self._parse_idx(p)
            return self._update_series(idx, body)
        return self._send(404, {"error": "Not Found"})

    # ---------- DELETE ----------
    def do_DELETE(self):
        u = urllib.parse.urlparse(self.path)
        p = u.path
        if p.startswith("/api/series/"):
            idx = self._parse_idx(p)
            return self._delete_series(idx)
        return self._send(404, {"error": "Not Found"})

    # ---------- 业务方法 ----------
    def _parse_idx(self, p):
        """路径 /api/series/{idx} 解析 idx。"""
        try:
            return int(p.rsplit("/", 1)[-1])
        except Exception:
            return -1

    def _add_series(self, body):
        required = ("mikan_id", "name")
        for k in required:
            if k not in body:
                return self._send(400, {"error": f"缺字段: {k}"})
        cfg = load_list()
        new_item = {
            "mikan_id": int(body["mikan_id"]),
            "name": body["name"],
            "subtitle_groups": body.get("subtitle_groups", []),
            "language": body.get("language", "简体"),
            "embedded_only": body.get("embedded_only", True),
            "save_path": body.get("save_path", f"/{body['name']}"),
            "last_episode": "0",
            "last_check": None,
            "last_check_status": None,
            "last_check_detail": None,
            "cover_url": body.get("cover_url"),
            "added_at": dt.date.today().isoformat(),
            "notes": body.get("notes", ""),
        }
        cfg.setdefault("series", []).append(new_item)
        save_list(cfg)
        return self._send(201, {"ok": True, "series": new_item})

    def _update_series(self, idx, body):
        cfg = load_list()
        if idx < 0 or idx >= len(cfg.get("series", [])):
            return self._send(404, {"error": "索引越界"})
        s = cfg["series"][idx]
        for k in ("subtitle_groups", "language", "embedded_only",
                  "save_path", "notes", "cover_url"):
            if k in body:
                s[k] = body[k]
        save_list(cfg)
        return self._send(200, {"ok": True, "series": s})

    def _delete_series(self, idx):
        cfg = load_list()
        if idx < 0 or idx >= len(cfg.get("series", [])):
            return self._send(404, {"error": "索引越界"})
        removed = cfg["series"].pop(idx)
        save_list(cfg)
        return self._send(200, {"ok": True, "removed": removed["name"]})

    def _refresh(self):
        """同步触发 auto_dl.py 一次，返回运行状态。"""
        try:
            r = subprocess.run([PY, "-u", os.path.join(BASE, "auto_dl.py")],
                               capture_output=True, text=True, timeout=180,
                               cwd=BASE)
            state = self._read_state()
            state["stdout"] = r.stdout[-2000:]
            state["stderr"] = r.stderr[-1000:]
            return self._send(200, state)
        except subprocess.TimeoutExpired:
            return self._send(504, {"error": "执行超时"})
        except Exception as e:
            return self._send(500, {"error": str(e)})

    def _scan_qr(self):
        """触发扫码登录脚本（后台）。"""
        try:
            subprocess.Popen([PY, "-u", os.path.join(BASE, "qr_cloud_dl.py")],
                             cwd=BASE,
                             stdout=open(os.path.join(BASE, ".qr_login.log"), "w"),
                             stderr=subprocess.STDOUT)
            return self._send(200, {"ok": True, "msg": "扫码脚本已启动，请查看 login_qr.png"})
        except Exception as e:
            return self._send(500, {"error": str(e)})

    def _read_state(self):
        if not os.path.exists(RUN_LOG):
            return {"status": "NEVER", "msg": "尚未运行过"}
        try:
            return json.load(open(RUN_LOG, encoding="utf-8"))
        except Exception as e:
            return {"status": "GENERIC", "msg": f"状态文件读取失败: {e}"}

    # ---------- 静态文件 ----------
    def _serve_static(self, name, default_ctype):
        path = os.path.join(STATIC_DIR, name)
        if not os.path.exists(path):
            return self._send(404, {"error": f"static file not found: {name}"})
        ext = os.path.splitext(name)[1].lower()
        ctypes = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".svg": "image/svg+xml",
        }
        ctype = ctypes.get(ext) or default_ctype or "application/octet-stream"
        body = open(path, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def find_free_port(start=PORT_DEFAULT):
    """端口被占用就找下一个空闲。"""
    for p in range(start, start + 10):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind((HOST, p))
            s.close()
            return p
        except OSError:
            continue
    return start


def main():
    port = find_free_port()
    os.makedirs(STATIC_DIR, exist_ok=True)
    server = ThreadingHTTPServer((HOST, port), Handler)
    url = f"http://localhost:{port}"
    print(f"Web 控制台已启动: {url}")
    print(f"工作目录: {BASE}")
    print("按 Ctrl+C 退出")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n关闭中...")
        server.shutdown()


if __name__ == "__main__":
    main()