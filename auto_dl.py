# -*- coding: utf-8 -*-
"""
每周追番流水线（自动化入口）
流程: 加载追番列表 → 网页端登录态自检 → 遍历番剧抓蜜柑详情页 →
      筛选字幕组/语言 → 对比已抓话数 → 提交新增磁力 → 回读任务状态 →
      更新清单 → 清理临时文件
失败语义（便于 automation 通知）:
  QRCODE_NEEDED  登录态失效，需要扫码
  NET_ERROR       网页端接入失败
  NO_UPDATES      本周无新话（正常，不算失败）
  GENERIC         其他错误
"""
import datetime as dt
import http.cookiejar
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from filelock import FileLock

BASE = os.path.dirname(os.path.abspath(__file__))
LIST_FILE = os.path.join(BASE, "追番列表.json")
LIST_LOCK = os.path.join(BASE, ".list.lock")
COOKIE_FILE = os.path.join(BASE, ".baidu_cookies.json")
QR_FILE = os.path.join(BASE, "login_qr.png")
RUN_LOG = os.path.join(BASE, ".auto_dl_state.json")
PROXY_WEB = "http://127.0.0.1:7897"  # Clash 出口

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# ---------- 网络工具 ----------
def make_opener(proxy=None):
    jar = http.cookiejar.CookieJar()
    handlers = [urllib.request.HTTPCookieProcessor(jar)]
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        handlers.append(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers), jar


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


# ---------- 蜜柑抓取（走代理） ----------
def fetch_mikan(mikan_id: int):
    """返回 HTML 文本。失败抛异常。"""
    url = f"https://mikanani.me/Home/Bangumi/{mikan_id}"
    op, _ = make_opener(PROXY_WEB)
    data = req(op, url, referer="https://mikanani.me/", timeout=30)
    return data.decode("utf-8", "replace")


def parse_mikan(html_txt: str):
    """复用 parse_mikan 逻辑，返回 strict 列表（简体内嵌）。"""
    import html as htmllib
    chunks = html_txt.split('<tr>')
    items = []
    for c in chunks:
        if 'js-episode-select' not in c:
            continue
        m = re.search(r'data-magnet="([^"]+)"', c)
        if not m:
            continue
        magnet = htmllib.unescape(m.group(1))
        a = re.search(r'<a class="magnet-link-wrap"[^>]*>(.*?)</a>', c, re.S)
        title = htmllib.unescape(re.sub(r'<[^>]+>', '', a.group(1))).strip() if a else ''
        size = re.search(r'<td>\s*([\d.]+\s*[KMGT]i?B)\s*</td>', c)
        items.append({
            'title': title,
            'magnet': magnet,
            'infohash': magnet.split('btih:')[1].split('&')[0] if 'btih:' in magnet else '',
            'size': size.group(1) if size else '',
        })
    # 去重
    seen, uniq = set(), []
    for it in items:
        if it['infohash'] in seen:
            continue
        seen.add(it['infohash'])
        uniq.append(it)

    def subgroup(t):
        m = re.search(r'[【\[]([^】\]]*?字幕组|[^】\]]*?压制组|[^】\]]*?字幕社)[】\]]', t)
        return m.group(1) if m else (re.search(r'[【\[]([^】\]]*?)[】\]]', t).group(1) if re.search(r'[【\[]([^】\]]*?)[】\]]', t) else '未知')

    def ep_of(t):
        m = re.search(r'[\[［]\s*(\d{1,3}(?:\.\d)?)\s*v?(\d)?\s*[\]］]', t)
        return (m.group(1), int(m.group(2)) if m and m.group(2) else 0) if m else ('?', 0)

    for it in uniq:
        t = it['title']
        it['group'] = subgroup(t)
        it['embedded'] = '内嵌' in t
        it['chs'] = any(k in t for k in ('简体', '简日', '简中', 'CHS', 'GB'))
        it['soft'] = '外挂' in t
        ep, ver = ep_of(t)
        it['ep'] = ep
        it['ver'] = ver
    return [i for i in uniq if i['embedded'] and i['chs'] and not i['soft']]


def select_best(items, preferred_groups):
    """每话选一条：优先首选字幕组，其次最早发布版本（v1 优先于 v2）。"""
    by_ep = {}
    for it in items:
        ep = it['ep']
        if ep == '?':
            continue
        # 优先首选字幕组
        score = (0 if it['group'] in preferred_groups else 1, it['ver'])
        if ep not in by_ep or score < by_ep[ep][0]:
            by_ep[ep] = (score, it)
    return [by_ep[ep][1] for ep in sorted(by_ep, key=lambda x: (len(x), x))]


# ---------- 百度网盘 ----------
def baidu_opener(cookies):
    jar = http.cookiejar.CookieJar()
    for k, v in cookies.items():
        ck = http.cookiejar.Cookie(0, k, v, None, False, ".baidu.com", True, True,
                                   "/", True, False, None, False, None, None, {}, False)
        jar.set_cookie(ck)
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(jar)), jar


def check_login(opener):
    """True=登录态有效"""
    try:
        u = ('https://pan.baidu.com/api/gettemplatevariable?clienttype=0&app_id=250528&web=1'
             '&fields=' + urllib.parse.quote('["bdstoken"]'))
        d = json.loads(req(opener, u, referer="https://pan.baidu.com/disk/main", timeout=15))
        return bool((d.get("result") or {}).get("bdstoken"))
    except Exception:
        return False


def get_bdstoken(opener):
    u = ('https://pan.baidu.com/api/gettemplatevariable?clienttype=0&app_id=250528&web=1'
         '&fields=' + urllib.parse.quote('["bdstoken"]'))
    d = json.loads(req(opener, u, referer="https://pan.baidu.com/disk/main", timeout=15))
    return (d.get("result") or {}).get("bdstoken")


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
        "need_task_info": "1", "status_type": "0", "start": "0", "limit": "50",
    })
    body = b"start=0&limit=50"
    raw = req(opener, f"https://pan.baidu.com/rest/2.0/services/cloud_dl?{q}",
                      data=body, referer="https://pan.baidu.com/disk/main")
    return json.loads(raw)


# ---------- 主流程 ----------
def today():
    return dt.date.today().isoformat()


def emit(status, msg="", **extra):
    """写出运行状态文件，automation 之后读这个文件向你汇报。"""
    out = {"status": status, "msg": msg, "date": today(),
           "time": dt.datetime.now().isoformat(), **extra}
    json.dump(out, open(RUN_LOG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[{status}] {msg}", flush=True)


def main():
    if not os.path.exists(LIST_FILE):
        emit("GENERIC", "未找到追番列表")
        return
    cfg = json.load(open(LIST_FILE, encoding="utf-8"))

    # 1) 登录态自检
    if not os.path.exists(COOKIE_FILE):
        emit("QRCODE_NEEDED", "无缓存登录态，请扫码")
        return
    cookies = json.load(open(COOKIE_FILE, encoding="utf-8"))
    op, _ = baidu_opener(cookies)
    if not check_login(op):
        os.remove(COOKIE_FILE)
        emit("QRCODE_NEEDED", "登录态已失效，请扫码")
        return

    token = get_bdstoken(op)
    if not token:
        emit("QRCODE_NEEDED", "无法获取 bdstoken，登录态可能失效")
        return

    # 2) 遍历各番剧
    submitted = []
    new_episodes_count = 0
    no_change = True
    for s in cfg["series"]:
        print(f"\n--- {s['name']} (#{s['mikan_id']}) ---", flush=True)
        try:
            html_txt = fetch_mikan(s["mikan_id"])
        except Exception as e:
            print(f"  抓取失败: {e}")
            continue
        items = parse_mikan(html_txt)
        best = select_best(items, s.get("subtitle_groups", []))
        last_ep = str(s.get("last_episode", "0"))
        new_items = [i for i in best if i["ep"] != "?" and _ep_gt(i["ep"], last_ep)]
        if not new_items:
            print(f"  无新话（已抓至第{last_ep}话）")
            continue
        no_change = False
        for it in new_items:
            print(f"  新话: 第{it['ep']}话 {it['group']} {it['size']} -> 提交", flush=True)
            try:
                r = add_task(op, token, it["magnet"], s["save_path"])
                errno = r.get("errno")
                if errno == 0:
                    submitted.append({"series": s["name"],
                                       "ep": it["ep"], "group": it["group"],
                                       "size": it["size"], "errno": 0,
                                       "task_id": r.get("task_id")})
                    s["last_episode"] = it["ep"]
                    new_episodes_count += 1
                else:
                    submitted.append({"series": s["name"],
                                       "ep": it["ep"], "errno": errno,
                                       "raw": json.dumps(r, ensure_ascii=False)[:200]})
                    print(f"    提交返回 errno={errno} raw={r}")
            except Exception as e:
                submitted.append({"series": s["name"],
                                   "ep": it["ep"], "errno": "EXC",
                                   "msg": str(e)})
                print(f"    异常: {e}")
            time.sleep(2)
        s["last_check"] = today()

    # 3) 回读任务状态
    task_summary = []
    if submitted:
        time.sleep(8)
        try:
            lt = list_tasks(op, token)
            info = lt.get("task_info") or {}
            for s in submitted:
                tid = s.get("task_id")
                if tid:
                    t = info.get(str(tid)) or {}
                    s["task_status"] = t.get("status")
                    s["task_name"] = t.get("task_name")
                    s["task_result"] = (t.get("file_size") or t.get("result"))
        except Exception as e:
            print(f"  任务状态读取失败: {e}")

    # 4) 持久化更新后的清单（加锁，避免与 web 并发写冲突）
    with FileLock(LIST_LOCK, timeout=15):
        json.dump(cfg, open(LIST_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # 5) 清理临时件
    for p in (QR_FILE,):
        if os.path.exists(p):
            try: os.remove(p)
            except Exception: pass

    # 6) 出状态
    if no_change:
        emit("NO_UPDATES", "本周无新话", submitted=submitted)
    else:
        emit("OK", f"已提交 {new_episodes_count} 条新话磁力", submitted=submitted)


def _ep_gt(a, b):
    """话数比较: a > b"""
    try:
        return float(a) > float(b)
    except Exception:
        return a > b


if __name__ == "__main__":
    main()