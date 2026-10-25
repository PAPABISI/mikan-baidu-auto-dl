# -*- coding: utf-8 -*-
"""
百度网盘离线下载提交脚本（纯标准库）
流程: 扫码登录(passport API) -> 取 bdstoken -> cloud_dl.add_task 提交磁力 -> list_task 校验
不依赖浏览器, cookie 由脚本自行持有。
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import http.cookiejar

BASE = os.path.dirname(os.path.abspath(__file__))
QR_PATH = os.path.join(BASE, "login_qr.png")
COOKIE_FILE = os.path.join(BASE, ".baidu_cookies.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

MAGNETS = [
    ("第01话v2", "magnet:?xt=urn:btih:8c67c88e94481c38ac2302b74f65534937dc1ae5"),
    ("第02话",   "magnet:?xt=urn:btih:44973df486878fbaf21762b66cdf139c6c58f6bf"),
    ("第03话",   "magnet:?xt=urn:btih:0f57ffc34048fae847998f3f4ff18a8d87f9ec1d"),
]
SAVE_PATH = "/FX战士久留美"


def make_opener():
    """直连优先（百度国内可达）；失败则由调用方决定是否走环境代理。"""
    jar = http.cookiejar.CookieJar()
    direct = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(jar))
    return direct, jar


def req(opener, url, data=None, referer=None, timeout=20):
    r = urllib.request.Request(url, data=data)
    r.add_header("User-Agent", UA)
    r.add_header("Accept", "*/*")
    if referer:
        r.add_header("Referer", referer)
    with opener.open(r, timeout=timeout) as resp:
        return resp.read()


def strip_callback(raw: bytes) -> dict:
    txt = raw.decode("utf-8", "replace").strip()
    m = re.match(r"^[^(]*\((.*)\)\s*;?$", txt, re.S)
    return json.loads(m.group(1) if m else txt)


def qr_login(opener, jar):
    """扫码登录, 返回是否成功。成功时 jar 内含 BDUSS/STOKEN。"""
    print("[1] 请求二维码...")
    tpl = "netdisk"
    api = "97" + "e634c9a44b6e2d0abdf1cd0c1b34e65e"  # netdisk tpl 公开 apikey
    url = (f"https://passport.baidu.com/v2/api/getqrcode?lp=pc&qrloginfrom=pc"
           f"&qrcode=1&tpl={tpl}&apikey={api}&charset=UTF-8&callback=jsonp")
    d = strip_callback(req(opener, url, referer="https://pan.baidu.com/"))
    sign, imgurl = d.get("sign"), d.get("imgurl")
    if not sign or not imgurl:
        print("   获取二维码失败:", d)
        return False
    if imgurl.startswith("//"):
        imgurl = "https:" + imgurl
    elif not imgurl.startswith("http"):
        imgurl = "https://" + imgurl
    img = req(opener, imgurl)
    open(QR_PATH, "wb").write(img)
    print(f"[2] 二维码已保存: {QR_PATH}")
    print("    请用手机 百度App 或 百度网盘App 扫码并确认登录（约3分钟内）...")
    print("QR_READY", flush=True)

    # 轮询登录结果
    deadline = time.time() + 360
    poll_count = 0
    while time.time() < deadline:
        try:
            u = (f"https://passport.baidu.com/channel/unicast?channel_id={sign}"
                 f"&tpl={tpl}&apikey={api}&callback=jsonp&charset=UTF-8")
            d = strip_callback(req(opener, u, referer="https://pan.baidu.com/", timeout=35))
            cv = d.get("channel_v")
            if cv:
                v = json.loads(cv).get("v") if isinstance(cv, str) else cv.get("v")
                if v:
                    print("[3] 扫码确认成功, 换取完整凭证...")
                    lu = (f"https://passport.baidu.com/v3/login/main/qrbdusslogin?lp=pc"
                          f"&qrloginfrom=pc&tpl={tpl}&loginmerge=true&bduss={v}"
                          f"&u={urllib.parse.quote('https://pan.baidu.com/disk/main')}"
                          f"&callback=jsonp")
                    req(opener, lu, referer="https://pan.baidu.com/")
                    cookies = {c.name: c.value for c in jar}
                    if "BDUSS" in cookies:
                        json.dump(cookies, open(COOKIE_FILE, "w"))
                        print("    已获得 BDUSS/STOKEN 并缓存")
                        return True
                    # 保底: v 本身就是 BDUSS
                    cookies["BDUSS"] = v
                    json.dump(cookies, open(COOKIE_FILE, "w"))
                    return True
        except Exception as e:
            poll_count += 1
            if poll_count % 5 == 0:
                print(f"    等待扫码确认中... ({poll_count} 次轮询)", flush=True)
        time.sleep(3)
    print("    扫码超时（6分钟内未收到确认信号）")
    return False


def get_bdstoken(opener):
    u = ('https://pan.baidu.com/api/gettemplatevariable?clienttype=0&app_id=250528&web=1'
         '&fields=' + urllib.parse.quote('["bdstoken"]'))
    d = json.loads(req(opener, u, referer="https://pan.baidu.com/disk/main"))
    tok = (d.get("result") or {}).get("bdstoken")
    print("[4] bdstoken:", (tok[:8] + "...") if tok else f"获取失败: {d}")
    return tok


def add_task(opener, token, magnet, save_path):
    q = urllib.parse.urlencode({
        "method": "add_task", "app_id": "250528", "bdstoken": token,
        "clienttype": "0", "web": "1", "channel": "chunlei",
    })
    body = urllib.parse.urlencode({
        "method": "add_task", "app_id": "250528",
        "file_sha1": "", "save_path": save_path,
        "selected_idx": "", "task_from": "1",
        "source_url": magnet, "type": "2",
    }).encode()
    raw = req(opener, f"https://pan.baidu.com/rest/2.0/services/cloud_dl?{q}",
              data=body, referer="https://pan.baidu.com/disk/main")
    return json.loads(raw)


def list_tasks(opener, token):
    q = urllib.parse.urlencode({
        "method": "list_task", "app_id": "250528", "bdstoken": token,
        "clienttype": "0", "web": "1", "channel": "chunlei",
        "start": "0", "limit": "20", "need_task_info": "1", "status_type": "0",
    })
    raw = req(opener, f"https://pan.baidu.com/rest/2.0/services/cloud_dl?{q}",
              data=b"start=0&limit=20", referer="https://pan.baidu.com/disk/main")
    return json.loads(raw)


def main():
    opener, jar = make_opener()

    # 复用已缓存的凭证
    if os.path.exists(COOKIE_FILE):
        saved = json.load(open(COOKIE_FILE))
        for k, v in saved.items():
            ck = http.cookiejar.Cookie(0, k, v, None, False, ".baidu.com", True, True,
                                       "/", True, False, None, False, None, None, {}, False)
            jar.set_cookie(ck)
        print("[0] 使用缓存的登录凭证")
    else:
        if not qr_login(opener, jar):
            sys.exit(2)

    token = get_bdstoken(opener)
    if not token:
        print("FAIL: 无法获取 bdstoken（登录态无效）")
        sys.exit(3)

    print(f"[5] 提交 {len(MAGNETS)} 条磁力到 {SAVE_PATH} ...")
    task_ids = []
    for name, mag in MAGNETS:
        try:
            r = add_task(opener, token, mag, SAVE_PATH)
            errno, tid = r.get("errno"), r.get("task_id")
            print(f"    {name}: errno={errno} task_id={tid}")
            if errno == 0 and tid:
                task_ids.append(tid)
            else:
                print(f"        响应: {r}")
        except Exception as e:
            print(f"    {name}: 异常 {e}")
        time.sleep(1)

    print("[6] 回读任务状态...")
    try:
        lt = list_tasks(opener, token)
        info = lt.get("task_info") or {}
        for tid in task_ids:
            t = info.get(str(tid))
            if t:
                print(f"    task {tid}: status={t.get('status')} "
                      f"name={t.get('task_name')} result={t.get('file_size','?')}")
            else:
                print(f"    task {tid}: 未在列表中（可能已秒失败或被过滤）")
        if not info:
            print("    原始响应:", json.dumps(lt, ensure_ascii=False)[:500])
    except Exception as e:
        print("    查询异常:", e)

    print("DONE")


if __name__ == "__main__":
    main()
