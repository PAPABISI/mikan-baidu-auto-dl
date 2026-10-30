"""百度网盘公共客户端：登录检查、离线任务、分页目录。仅使用标准库。"""
import http.cookiejar
import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from storage import BASE, read_json

COOKIE_FILE = os.path.join(BASE, ".baidu_cookies.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"
REFERER = "https://pan.baidu.com/disk/main"
NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, ConnectionError, socket.timeout)


class LoginRequired(Exception):
    pass


class APIError(Exception):
    pass


def safe_error(error):
    # HTTP 错误可能把含 BDUSS/bdstoken 的请求 URL 放进异常，不能传给前端或日志。
    return re.sub(r"https?://[^\s'\"<>]+", "[请求地址已隐藏]", str(error))[:300]


def make_opener(cookies=None, proxy=None):
    jar = http.cookiejar.CookieJar()
    for key, value in (cookies or {}).items():
        if not isinstance(value, str):
            continue
        jar.set_cookie(http.cookiejar.Cookie(
            0, key, value, None, False, ".baidu.com", True, True,
            "/", True, True, None, False, None, None, {}, False))
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {}),
                urllib.request.HTTPCookieProcessor(jar)]
    return urllib.request.build_opener(*handlers), jar


def pan_cookies(jar):
    """仅导出网盘请求实际会携带的 cookie，避免 passport 同名值覆盖网盘值。"""
    request = urllib.request.Request(REFERER)
    jar.add_cookie_header(request)
    cookies = {}
    for part in (request.get_header("Cookie") or "").split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and not key.startswith("$"):
            cookies.setdefault(key, value)
    return cookies


def req(opener, url, data=None, referer=REFERER, timeout=20, retry=True):
    """只重试可安全重复的读取。提交调用必须 retry=False。"""
    for attempt in range(3 if retry else 1):
        request = urllib.request.Request(url, data=data, headers={
            "User-Agent": UA, "Accept": "*/*", "Referer": referer})
        try:
            with opener.open(request, timeout=timeout) as response:
                return response.read()
        except NETWORK_ERRORS as error:
            if isinstance(error, urllib.error.HTTPError) and error.code < 500 and error.code != 429:
                raise
            if attempt == (2 if retry else 0):
                raise
            time.sleep(0.5 * (2 ** attempt))


def errno(data):
    return data.get("errno", data.get("error_code"))


def check_response(data):
    code = errno(data)
    if code is not None and str(code) in ("-6", "110", "111"):
        raise LoginRequired("百度登录态已失效，请重新扫码")
    if code is not None and str(code) != "0":
        raise APIError(f"百度接口返回错误码 {code}")
    return data


class BaiduClient:
    def __init__(self, cookies=None):
        if cookies is None:
            try:
                cookies = read_json(COOKIE_FILE, {})
            except ValueError:
                raise LoginRequired("登录缓存损坏，请重新扫码") from None
        if not isinstance(cookies, dict) or not cookies.get("BDUSS"):
            raise LoginRequired("没有缓存登录态，请扫码")
        self.opener, self.jar = make_opener(cookies)
        self.token = None

    def login_info(self):
        fields = urllib.parse.quote('["bdstoken","username","dp"]')
        url = "https://pan.baidu.com/api/gettemplatevariable?clienttype=0&app_id=250528&web=1&fields=" + fields
        result = {}
        try:
            data = check_response(json.loads(req(self.opener, url, timeout=15)))
            result = data.get("result") or {}
            self.token = result.get("bdstoken")
        except (LoginRequired, APIError, json.JSONDecodeError):
            # 该预检接口可能拒绝有效会话，不能仅凭它的 -6 要求用户反复扫码。
            pass
        if not self.token:
            probe = "https://pan.baidu.com/api/list?dir=%2F&num=1&page=1&clienttype=0&app_id=250528&web=1"
            data = json.loads(req(self.opener, probe, timeout=15))
            try:
                check_response(data)
            except LoginRequired:
                raise LoginRequired("网盘暂未接受该登录会话，请稍后重试；若仍失败再重新扫码") from None
            if str(errno(data)) != "0" or not isinstance(data.get("list"), list):
                raise APIError("网盘登录验证响应异常，无法确认登录结果")
            # 目录已证实登录有效，再尝试从网盘页面取得离线接口所需 token。
            try:
                # 未完成网盘回跳的会话访问登录页时可能被清除 cookie，探测不能破坏已验证的会话。
                page_opener, page_jar = make_opener(pan_cookies(self.jar))
                page = req(page_opener, REFERER, timeout=15).decode("utf-8", "replace")
                match = re.search(r'''["']bdstoken["']\s*:\s*["']([0-9a-f]{32})["']''', page, re.I)
                if match:
                    self.token = match.group(1)
                    self.opener, self.jar = page_opener, page_jar
            except NETWORK_ERRORS:
                pass
        info = {"logged_in": True, "status": "OK", "username": result.get("username", "")}
        if not self.token:
            info.update(status="TOKEN_UNAVAILABLE", warning="登录有效，但网盘尚未下发离线下载凭据，请稍后重试")
        self.login_profile = info
        return dict(info)

    def cloud(self, method, params=None, retry=True):
        if not self.token:
            self.login_info()
        if not self.token:
            raise APIError("登录有效，但尚未取得离线下载凭据，请稍后重试，无需反复扫码")
        query = urllib.parse.urlencode({"method": method, "app_id": "250528",
                                       "bdstoken": self.token, "clienttype": "0", "web": "1",
                                       "channel": "chunlei"})
        if method == "query_task":
            query += "&" + urllib.parse.urlencode(params or {})
            body = None
        else:
            body = urllib.parse.urlencode(params or {}).encode()
        return json.loads(req(self.opener, "https://pan.baidu.com/rest/2.0/services/cloud_dl?" + query,
                              data=body, retry=retry))

    def add_task(self, magnet, save_path):
        return self.cloud("add_task", {"source_url": magnet, "save_path": save_path,
                                      "type": "4", "task_from": "1", "selected_idx": ""}, retry=False)

    def query_tasks(self, task_ids):
        """按 ID 查询，避免 list_task 最近 20/50 条的分页截断。"""
        result = {}
        ids = list(dict.fromkeys(str(t) for t in task_ids if t))
        for start in range(0, len(ids), 50):
            data = check_response(self.cloud("query_task", {
                "task_ids": ",".join(ids[start:start + 50]), "op_type": "1"}))
            info = data.get("task_info") or {}
            if isinstance(info, list):
                result.update({str(t["task_id"]): t for t in info if t.get("task_id")})
            elif isinstance(info, dict):
                result.update(info)
            else:
                raise APIError("任务查询响应格式异常")
        return result

    def list_files(self, path):
        if not path.startswith("/") or ".." in path.split("/"):
            raise ValueError("请提供以 / 开头的网盘目录")
        files, seen = [], set()
        for page in range(1, 1001):
            query = urllib.parse.urlencode({"order": "name", "desc": 0, "clienttype": 0,
                                           "app_id": 250528, "web": 1, "dir": path,
                                           "page": page, "num": 100})
            data = check_response(json.loads(req(self.opener, "https://pan.baidu.com/api/list?" + query)))
            batch = data.get("list")
            if not isinstance(batch, list):
                raise APIError("目录响应缺少文件列表")
            added = 0
            for item in batch:
                key = str(item.get("fs_id") or item.get("path") or item.get("server_filename"))
                if key not in seen:
                    seen.add(key)
                    added += 1
                    files.append({"name": item.get("server_filename") or item.get("filename"),
                                  "size": item.get("size", 0), "isdir": item.get("isdir", 0),
                                  "path": item.get("path"), "fs_id": item.get("fs_id")})
            if not batch or str(data.get("has_more")) in ("0", "False") or (len(batch) < 100 and not data.get("has_more")):
                return files
            if not added:
                raise APIError("目录分页未前进，请稍后重试")
        raise APIError("目录超过 100000 项，请改查子目录")
