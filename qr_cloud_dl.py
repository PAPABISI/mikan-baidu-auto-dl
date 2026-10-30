# -*- coding: utf-8 -*-
"""百度扫码登录。只建立登录态，下载由 auto_dl.py 按追番配置执行。"""
import argparse
import ast
import datetime as dt
import http.cookiejar
import json
import os
import re
import time
import urllib.parse
import uuid

from baidu import BaiduClient, COOKIE_FILE, LoginRequired, NETWORK_ERRORS, make_opener, pan_cookies, req, safe_error
from storage import BASE, read_json, write_json

QR_PATH = os.path.join(BASE, "login_qr.png")
DIAGNOSTIC_FILE = os.path.join(BASE, ".qr_diagnostics.json")
PENDING_FILE = os.path.join(BASE, ".baidu_pending_login.json")
TPL = "netdisk"
API_KEY = "97e634c9a44b6e2d0abdf1cd0c1b34e65e"


def exchange_error(raw):
    """只提取数字错误码；响应体可能包含凭据，禁止写入日志或前端。"""
    text = raw.decode("utf-8", "replace")
    try:
        data = strip_callback(raw)
        error = data.get("errInfo") or {}
        return str(error.get("no", data.get("errno", data.get("err_no", "0"))))
    except (ValueError, AttributeError):
        match = re.search(r'''["'](?:no|err_no|errno)["']\s*:\s*["']?(-?\d+)''', text)
        return match.group(1) if match else None


def strip_callback(raw):
    text = raw.decode("utf-8", "replace").strip()
    match = re.match(r"^[^(]*\((.*)\)\s*;?$", text, re.S)
    payload = match.group(1) if match else text
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        # 部分 passport 响应使用单引号对象；只解析字面量，不执行返回的 JavaScript。
        try:
            value = ast.literal_eval(payload)
        except (ValueError, SyntaxError, TypeError):
            raise ValueError("百度登录响应格式异常") from None
        if not isinstance(value, dict):
            raise ValueError("百度登录响应格式异常")
        return value


def complete_login_redirect(opener, raw):
    """与官方登录脚本一样完成 data.u 回跳，使网盘收到完整登录会话。"""
    data = strip_callback(raw).get("data") or {}
    target = data.get("u")
    if not target:
        return False
    if not isinstance(target, str):
        raise ValueError("百度登录回跳地址格式异常")
    for _ in range(4):
        if target.startswith(("https://", "http://", "/")):
            break
        decoded = urllib.parse.unquote(target)
        if decoded == target:
            break
        target = decoded
    parsed = urllib.parse.urlsplit(urllib.parse.urljoin("https://pan.baidu.com/", target))
    hostname = parsed.hostname or ""
    if (parsed.scheme not in ("https", "http") or not (hostname == "baidu.com" or hostname.endswith(".baidu.com"))
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise ValueError("百度返回了不支持的登录回跳地址")
    # 部分响应仍给出 http 百度地址，只通过 HTTPS 完成回跳。
    target = urllib.parse.urlunsplit(parsed._replace(scheme="https"))
    req(opener, target, referer="https://pan.baidu.com/", retry=False)
    return True


def save_pending(jar, raw):
    """保存短时交换会话以便继续验证，敏感文件必须被 Git 忽略。"""
    data = strip_callback(raw).get("data") or {}
    fields = ("version", "name", "value", "domain", "domain_specified", "domain_initial_dot",
              "path", "path_specified", "secure", "expires", "discard")
    write_json(PENDING_FILE, {"created_at": time.time(), "return_url": data.get("u"),
                              "cookies": [{key: getattr(cookie, key) for key in fields} for cookie in jar]})


def load_pending():
    pending = read_json(PENDING_FILE)
    if not pending:
        return None
    if time.time() - pending["created_at"] > 360:
        os.remove(PENDING_FILE)
        return None
    opener, jar = make_opener()
    for data in pending["cookies"]:
        jar.set_cookie(http.cookiejar.Cookie(port=None, port_specified=False, comment=None,
                                             comment_url=None, rest={}, rfc2109=False, **data))
    raw = json.dumps({"data": {"u": pending.get("return_url")}}).encode()
    return opener, jar, raw


def finish_login(opener, jar, raw, diagnose, update, ticket=None):
    data = strip_callback(raw).get("data") or {}
    target = urllib.parse.urlsplit(str(data.get("u") or ""))
    diagnose("REDIRECT", return_scheme=target.scheme, return_host=target.hostname,
             return_encoded="%" in str(data.get("u") or ""))
    followed = complete_login_redirect(opener, raw)
    diagnose("VERIFY", redirect_followed=followed, cookie_names=sorted({c.name for c in jar}))
    cookies = pan_cookies(jar)
    if not cookies.get("BDUSS") or (ticket and cookies["BDUSS"] == ticket):
        raise ValueError("扫码已确认，但百度未下发网盘登录凭据，请重新生成二维码")
    client = BaiduClient(cookies)
    info = client.login_info()
    write_json(COOKIE_FILE, cookies)
    if client.token and os.path.exists(PENDING_FILE):
        os.remove(PENDING_FILE)
    diagnose("READY", has_bduss=True, token_available=bool(client.token))
    update("LOGGED_IN", info.get("warning") or "登录成功", username=info.get("username", ""), qr_url="")
    return client


def ensure_login(qr_path=QR_PATH, on_state=None, force=False):
    diagnostics = {"stage": "CACHE", "time": dt.datetime.now().isoformat()}

    def diagnose(stage, **fields):
        diagnostics.update(stage=stage, **fields)
        write_json(DIAGNOSTIC_FILE, diagnostics)

    def update(state, message, **extra):
        if on_state:
            on_state(state=state, message=message, **extra)
        else:
            print(f"[{state}] {message}", flush=True)

    if not force:
        pending = load_pending()
        if pending:
            update("LOGGED_IN", "继续完成上一次扫码登录...", qr_url="")
            return finish_login(*pending, diagnose, update)
        try:
            client = BaiduClient()
            info = client.login_info()
            update("LOGGED_IN", "缓存登录态有效", username=info.get("username", ""), qr_url="")
            return client
        except LoginRequired:
            pass
        # 网络错误必须交给调用者处理，不能误删凭据、强迫重新扫码。

    opener, jar = make_opener()
    gid = str(uuid.uuid4()).upper()
    diagnose("REQUEST_QR")
    update("WAITING", "正在获取二维码...", qr_url="")
    # 与正式网页一样先建立百度会话；后续获取二维码、轮询与换票据共用同一个 jar。
    req(opener, "https://pan.baidu.com/", referer="https://pan.baidu.com/")
    timestamp = int(time.time() * 1000)
    query = urllib.parse.urlencode({"lp": "pc", "qrloginfrom": "pc", "qrcode": 1,
                                    "tpl": TPL, "apikey": API_KEY, "charset": "UTF-8", "callback": "jsonp",
                                    "gid": gid, "apiver": "v3", "tt": timestamp})
    data = strip_callback(req(opener, "https://passport.baidu.com/v2/api/getqrcode?" + query,
                              referer="https://pan.baidu.com/"))
    sign, image_url = data.get("sign"), data.get("imgurl")
    if not sign or not image_url:
        raise ValueError("百度没有返回有效二维码，请稍后重试")
    if image_url.startswith("//"):
        image_url = "https:" + image_url
    elif not image_url.startswith("http"):
        image_url = "https://" + image_url
    image = req(opener, image_url)
    os.makedirs(os.path.dirname(qr_path), exist_ok=True)
    with open(qr_path, "wb") as stream:
        stream.write(image)
    update("WAITING", "请用百度或百度网盘 App 扫码，并在手机确认登录",
           qr_ready=True, expires_at=(dt.datetime.now() + dt.timedelta(minutes=6)).isoformat())
    if not on_state:
        print("二维码文件：" + qr_path, flush=True)

    deadline = time.monotonic() + 360
    diagnose("POLL")
    while time.monotonic() < deadline:
        timestamp = int(time.time() * 1000)
        query = urllib.parse.urlencode({"channel_id": sign, "tpl": TPL, "apikey": API_KEY,
                                        "callback": "jsonp", "charset": "UTF-8", "gid": gid,
                                        "apiver": "v3", "tt": timestamp, "_": timestamp})
        try:
            data = strip_callback(req(opener, "https://passport.baidu.com/channel/unicast?" + query,
                                      referer="https://pan.baidu.com/", timeout=35, retry=False))
        except NETWORK_ERRORS:
            time.sleep(3)
            continue
        channel = data.get("channel_v")
        if channel and str(data.get("errno", 0)) == "0":
            channel = json.loads(channel) if isinstance(channel, str) else channel
            value = channel.get("v")
            if str(channel.get("status")) == "1":
                update("WAITING", "已扫码，请在手机上确认登录")
            if value and str(channel.get("status")) == "0":
                update("LOGGED_IN", "扫码已确认，正在验证凭据...", qr_url="")
                diagnose("EXCHANGE")
                timestamp = int(time.time() * 1000)
                query = urllib.parse.urlencode({
                    "lp": "pc", "qrloginfrom": "pc", "tpl": TPL, "loginmerge": "true",
                    "bduss": value, "u": urllib.parse.quote("https://pan.baidu.com/disk/main", safe=""), "callback": "jsonp",
                    "loginVersion": "v4", "qrcode": 1, "apiver": "v3", "alg": "v3",
                    "v": timestamp, "tt": timestamp, "time": timestamp // 1000,
                    "gid": gid, "traceid": ""})
                raw = req(opener, "https://passport.baidu.com/v3/login/main/qrbdusslogin?" + query,
                          referer="https://pan.baidu.com/", retry=False)
                code = exchange_error(raw)
                diagnose("EXCHANGE", exchange_code=code, cookie_names=sorted({c.name for c in jar}))
                if code not in (None, "0"):
                    raise ValueError(f"扫码已确认，但百度凭据交换失败（错误码 {code}），请重新生成二维码")
                # channel_v.v 是临时票据，不能作为 BDUSS 兜底。只接受服务器下发的正式 cookie。
                save_pending(jar, raw)
                return finish_login(opener, jar, raw, diagnose, update, ticket=value)
        time.sleep(3)
    raise TimeoutError("二维码已过期，请重新生成")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="忽略缓存，重新扫码")
    args = parser.parse_args()
    try:
        ensure_login(force=args.force)
        print("登录态已就绪，可运行 python auto_dl.py 或在控制台点击立即检查")
        return 0
    except Exception as error:
        print("登录失败：" + safe_error(error))
        return 2
    finally:
        if os.path.exists(QR_PATH):
            os.remove(QR_PATH)


if __name__ == "__main__":
    raise SystemExit(main())
