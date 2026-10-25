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
QR_PNG = os.path.join(STATIC_DIR, "qr.png")
QR_STATE_FILE = os.path.join(BASE, ".qr_state.json")

PROXY_WEB = "http://127.0.0.1:7897"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
PY = sys.executable
HOST, PORT_DEFAULT = "127.0.0.1", 8765

# 默认磁力（FX战士久留美 绿茶字幕组 三话）
DEFAULT_MAGNETS = [
    {"name": "第01话v2", "magnet": "magnet:?xt=urn:btih:8c67c88e94481c38ac2302b74f65534937dc1ae5"},
    {"name": "第02话",   "magnet": "magnet:?xt=urn:btih:44973df486878fbaf21762b66cdf139c6c58f6bf"},
    {"name": "第03话",   "magnet": "magnet:?xt=urn:btih:0f57ffc34048fae847998f3f4ff18a8d87f9ec1d"},
]
DEFAULT_SAVE_PATH = "/FX战士久留美"

# ============ 扫码会话全局状态 ============
_qr_lock = threading.Lock()
_qr_state = {
    "state": "IDLE",          # IDLE / WAITING / LOGGED_IN / SUBMITTING / DONE / FAILED
    "message": "",
    "qr_url": "",            # 浏览器可访问的二维码 URL
    "expires_at": "",
    "tasks": [],             # [{name, errno, task_id, status, task_name}]
    "started_at": "",
}


def _qr_set(state=None, message=None, qr_url=None, tasks=None, **extra):
    """更新扫码会话状态（线程安全）。"""
    with _qr_lock:
        if state: _qr_state["state"] = state
        if message is not None: _qr_state["message"] = message
        if qr_url is not None: _qr_state["qr_url"] = qr_url
        if tasks is not None: _qr_state["tasks"] = tasks
        if extra: _qr_state.update(extra)
        # 持久化
        try:
            json.dump(_qr_state, open(QR_STATE_FILE, "w", encoding="utf-8"),
                      ensure_ascii=False)
        except Exception:
            pass


def _qr_get():
    with _qr_lock:
        return dict(_qr_state)


def _pan_opener_from_file():
    """从缓存 cookie 构造 pan.baidu.com 可用的 opener。"""
    cookies = json.load(open(COOKIE_FILE, encoding="utf-8"))
    jar = http.cookiejar.CookieJar()
    for k, v in cookies.items():
        ck = http.cookiejar.Cookie(0, k, v, None, False, ".baidu.com", True, True,
                                   "/", True, False, None, False, None, None, {}, False)
        jar.set_cookie(ck)
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(jar))


def _qr_login_thread(magnets, save_path):
    """扫码登录线程：复用/获取 BDUSS → 提交磁力 → 回读任务。"""
    import time as _t

    def _mk_req(op):
        def _req(url, data=None, referer=None, timeout=20):
            r = urllib.request.Request(url, data=data)
            r.add_header("User-Agent", UA)
            r.add_header("Accept", "*/*")
            if referer:
                r.add_header("Referer", referer)
            with op.open(r, timeout=timeout) as resp:
                return resp.read()
        return _req

    try:
        _qr_set(state="WAITING", message="检查登录态...",
                tasks=[], started_at=dt.datetime.now().isoformat())
        bduss = None
        if os.path.exists(COOKIE_FILE):
            # 复用缓存登录态，跳过扫码
            try:
                op = _pan_opener_from_file()
                _req = _mk_req(op)
                f = urllib.parse.quote('["bdstoken","username","dp"]')
                u = ('https://pan.baidu.com/api/gettemplatevariable'
                     '?clienttype=0&app_id=250528&web=1&fields=' + f)
                d = json.loads(_req(u, referer="https://pan.baidu.com/disk/main"))
                if d.get("errno") == 0 and (d.get("result") or {}).get("bdstoken"):
                    uname = (d.get("result") or {}).get("username", "?")
                    _qr_set(state="LOGGED_IN",
                            message=f"缓存登录态有效（{uname}），直接提交")
                    _submit_all(_req, (d["result"]["bdstoken"]), magnets, save_path, _t)
                    return
            except Exception as e:
                _qr_set(message=f"缓存登录态失效（{e}），转扫码流程")

        # ---- 扫码流程 ----
        jar = http.cookiejar.CookieJar()
        op = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(jar))
        _req = _mk_req(op)

        def _cb(raw):
            txt = raw.decode("utf-8", "replace").strip()
            m = re.match(r"^[^(]*\((.*)\)\s*;?$", txt, re.S)
            return json.loads(m.group(1) if m else txt)

        # 1. 拿二维码
        tpl = "netdisk"
        api = "97e634c9a44b6e2d0abdf1cd0c1b34e65e"
        url = (f"https://passport.baidu.com/v2/api/getqrcode?lp=pc&qrloginfrom=pc"
               f"&qrcode=1&tpl={tpl}&apikey={api}&charset=UTF-8&callback=jsonp")
        d = _cb(_req(url, referer="https://pan.baidu.com/"))
        sign, imgurl = d.get("sign"), d.get("imgurl")
        if not sign or not imgurl:
            _qr_set(state="FAILED", message=f"获取二维码失败: {d}")
            return
        if imgurl.startswith("//"):
            imgurl = "https:" + imgurl
        elif not imgurl.startswith("http"):
            imgurl = "https://" + imgurl
        img = _req(imgurl)
        os.makedirs(STATIC_DIR, exist_ok=True)
        with open(QR_PNG, "wb") as f:
            f.write(img)
        expires = (dt.datetime.now() + dt.timedelta(minutes=6)).isoformat()
        _qr_set(state="WAITING",
                message="请用手机 百度App 或 百度网盘App 扫码并在手机上点确认登录",
                qr_url=f"/static/qr.png?ts={int(_t.time())}",
                expires_at=expires)

        # 2. 轮询扫码确认
        deadline = _t.time() + 360
        bduss = None
        while _t.time() < deadline:
            try:
                u = (f"https://passport.baidu.com/channel/unicast?channel_id={sign}"
                     f"&tpl={tpl}&apikey={api}&callback=jsonp&charset=UTF-8")
                d = _cb(_req(u, referer="https://pan.baidu.com/", timeout=35))
                cv = d.get("channel_v")
                if cv:
                    v = json.loads(cv).get("v") if isinstance(cv, str) else cv.get("v")
                    if v:
                        bduss = v
                        break
            except Exception:
                pass
            _t.sleep(3)
        if not bduss:
            _qr_set(state="FAILED", message="扫码超时（6分钟内未收到确认）")
            return
        _qr_set(state="LOGGED_IN", message="扫码成功，换取完整凭证中...")

        # 3. 换取完整凭证
        lu = (f"https://passport.baidu.com/v3/login/main/qrbdusslogin?lp=pc"
              f"&qrloginfrom=pc&tpl={tpl}&loginmerge=true&bduss={bduss}"
              f"&u={urllib.parse.quote('https://pan.baidu.com/disk/main')}"
              f"&callback=jsonp")
        _req(lu, referer="https://pan.baidu.com/")
        cookies = {c.name: c.value for c in jar}
        if "BDUSS" not in cookies:
            cookies["BDUSS"] = bduss
        # 缓存 cookie
        json.dump(cookies, open(COOKIE_FILE, "w"))
        _qr_set(state="LOGGED_IN", message="已拿到 BDUSS，获取 bdstoken...")

        # 4. 取 bdstoken（注意：fields 必须多字段，单字段 bdstoken 会被百度风控拦成 errno=-6）
        #    且必须用显式 .baidu.com 域的 opener（扫码 jar 的 cookie 绑在 passport 域）
        op = _pan_opener_from_file()
        _req = _mk_req(op)
        f = urllib.parse.quote('["bdstoken","username","dp"]')
        u = ('https://pan.baidu.com/api/gettemplatevariable?clienttype=0&app_id=250528&web=1'
             '&fields=' + f)
        d = json.loads(_req(u, referer="https://pan.baidu.com/disk/main"))
        token = (d.get("result") or {}).get("bdstoken")
        if not token:
            _qr_set(state="FAILED", message=f"bdstoken 获取失败: {d}")
            return
        _submit_all(_req, token, magnets, save_path, _t)
    except Exception as e:
        _qr_set(state="FAILED", message=f"内部异常: {e}")


def _submit_all(_req, token, magnets, save_path, _t):
    """用已就绪的 _req 提交磁力并回读状态。"""
    _qr_set(state="SUBMITTING", message=f"提交 {len(magnets)} 条磁力...")

    tasks = []
    for m in magnets:
        try:
            q = urllib.parse.urlencode({
                "method": "add_task", "app_id": "250528", "bdstoken": token,
                "clienttype": "0", "web": "1", "channel": "chunlei",
            })
            body = urllib.parse.urlencode({
                "method": "add_task", "app_id": "250528",
                "file_sha1": "", "save_path": save_path,
                "selected_idx": "", "task_from": "1",
                "source_url": m["magnet"], "type": "2",
            }).encode()
            raw = _req(f"https://pan.baidu.com/rest/2.0/services/cloud_dl?{q}",
                       data=body, referer="https://pan.baidu.com/disk/main")
            r = json.loads(raw)
            tasks.append({
                "name": m["name"], "errno": r.get("errno"),
                "task_id": r.get("task_id"), "status": None,
                "task_name": None, "raw": r,
            })
            _qr_set(state="SUBMITTING", tasks=tasks)
        except Exception as e:
            tasks.append({"name": m["name"], "errno": -999,
                          "status": None, "error": str(e)})
            _qr_set(state="SUBMITTING", tasks=tasks)
        _t.sleep(1)

    # 回读任务状态
    try:
        q = urllib.parse.urlencode({
            "method": "list_task", "app_id": "250528", "bdstoken": token,
            "clienttype": "0", "web": "1", "channel": "chunlei",
            "start": "0", "limit": "20", "need_task_info": "1", "status_type": "0",
        })
        raw = _req(f"https://pan.baidu.com/rest/2.0/services/cloud_dl?{q}",
                   data=b"start=0&limit=20",
                   referer="https://pan.baidu.com/disk/main")
        lt = json.loads(raw)
        info = lt.get("task_info") or {}
        for t in tasks:
            tid = str(t.get("task_id") or "")
            ti = info.get(tid)
            if ti:
                t["status"] = ti.get("status")
                t["task_name"] = ti.get("task_name")
    except Exception as e:
        _qr_set(state="DONE", tasks=tasks,
                message=f"磁力已提交，但回读任务状态失败: {e}")
        return
    _qr_set(state="DONE", tasks=tasks, message="流程完成")


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
        if p == "/api/qr/status":
            return self._send(200, _qr_get())
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
        if p == "/api/scan-qr" or p == "/api/qr/start":
            return self._qr_start(body)
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

    def _qr_start(self, body):
        """启动扫码登录+磁力提交流程（线程内异步执行）。"""
        s = _qr_get()
        if s["state"] in ("WAITING", "LOGGED_IN", "SUBMITTING"):
            return self._send(409, {"error": "已有扫码流程在进行中",
                                    "state": s})
        magnets = body.get("magnets") if body else None
        if not magnets:
            magnets = DEFAULT_MAGNETS
        save_path = (body or {}).get("save_path") or DEFAULT_SAVE_PATH
        # 重置状态
        _qr_set(state="WAITING", message="准备生成二维码...",
                qr_url="", expires_at="", tasks=[],
                started_at=dt.datetime.now().isoformat())
        threading.Thread(target=_qr_login_thread,
                         args=(magnets, save_path),
                         daemon=True).start()
        return self._send(200, {"ok": True, "msg": "扫码流程已启动",
                                "magnets": magnets, "save_path": save_path})

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