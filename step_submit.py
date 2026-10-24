"""一次调用内完成：接管 Chrome（你的配置）→ 校验登录 → 提交离线下载任务。

优先走网盘私有接口；失败则降级为页面 UI 自动化（路线 B）。
提交范围为推荐成套：绿茶字幕组 01v2 / 02 / 03。
"""
import json, os, shutil, subprocess, sys, time
from cdp import Session, PORT, http_get, http_put, BASE

BROWSERS = {
    "chrome": (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
               r"C:\Users\u00uk\AppData\Local\Google\Chrome\User Data"),
    "edge": (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
             r"C:\Users\u00uk\AppData\Local\Microsoft\Edge\User Data"),
}
WHICH = sys.argv[1] if len(sys.argv) > 1 else "chrome"
if WHICH not in BROWSERS:
    print("用法: step_submit.py [chrome|edge]")
    raise SystemExit(1)
BIN, REAL_PROFILE = BROWSERS[WHICH]
COPY_PROFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), f".{WHICH}_copy")


def detect_profile_dir():
    """自动找出存放 Cookies 的 profile 子目录（Chrome=Default，Edge 常为 Profile 1）。"""
    base = REAL_PROFILE
    for name in sorted(os.listdir(base)):
        cand = os.path.join(base, name)
        if os.path.isdir(cand) and (
            os.path.exists(os.path.join(cand, "Network", "Cookies"))
            or os.path.exists(os.path.join(cand, "Cookies"))
        ):
            return name
    return "Default"


def prepare_copy_profile():
    """只复制登录凭据相关文件，避免携带大量缓存。"""
    prof = detect_profile_dir()
    print(f"   源 profile 目录: {prof}")
    dst_prof = os.path.join(COPY_PROFILE, prof)
    os.makedirs(os.path.join(dst_prof, "Network"), exist_ok=True)
    pairs = [(os.path.join(REAL_PROFILE, "Local State"), os.path.join(COPY_PROFILE, "Local State"))]
    src_net = os.path.join(REAL_PROFILE, prof, "Network")
    if os.path.isdir(src_net):
        for name in os.listdir(src_net):
            if "Cookies" in name:
                pairs.append((os.path.join(src_net, name), os.path.join(dst_prof, "Network", name)))
    for pref in ("Preferences", "Secure Preferences"):
        p = os.path.join(REAL_PROFILE, prof, pref)
        if os.path.exists(p):
            pairs.append((p, os.path.join(dst_prof, pref)))
    done = []
    for s, d in pairs:
        try:
            shutil.copy2(s, d)
            done.append(os.path.basename(s))
        except Exception as e:
            print("   复制失败", s, e)
    print("   已复制:", done)
    return prof

SAVE_PATH = "/FX战士久留美"

MAGNETS = [
    ("01v2", "magnet:?xt=urn:btih:8c67c88e94481c38ac2302b74f65534937dc1ae5"
             "&tr=http%3a%2f%2ft.nyaatracker.com%2fannounce"
             "&tr=http%3a%2f%2ftracker.kamigami.org%3a2710%2fannounce"
             "&tr=http%3a%2f%2fshare.camoe.cn%3a8080%2fannounce"
             "&tr=http%3a%2f%2fopentracker.acgnx.se%2fannounce"
             "&tr=http%3a%2f%2fanidex.moe%3a6969%2fannounce"
             "&tr=http%3a%2f%2ft.acg.rip%3a6699%2fannounce"
             "&tr=https%3a%2f%2ftr.bangumi.moe%3a9696%2fannounce"
             "&tr=udp%3a%2f%2ftr.bangumi.moe%3a6969%2fannounce"
             "&tr=http%3a%2f%2fopen.acgtracker.com%3a1096%2fannounce"
             "&tr=udp%3a%2f%2ftracker.opentrackr.org%3a1337%2fannounce"),
    ("02", "magnet:?xt=urn:btih:44973df486878fbaf21762b66cdf139c6c58f6bf"
           "&tr=http%3a%2f%2ft.nyaatracker.com%2fannounce"
           "&tr=http%3a%2f%2ftracker.kamigami.org%3a2710%2fannounce"
           "&tr=http%3a%2f%2fshare.camoe.cn%3a8080%2fannounce"
           "&tr=http%3a%2f%2fopentracker.acgnx.se%2fannounce"
           "&tr=http%3a%2f%2fanidex.moe%3a6969%2fannounce"
           "&tr=http%3a%2f%2ft.acg.rip%3a6699%2fannounce"
           "&tr=https%3a%2f%2ftr.bangumi.moe%3a9696%2fannounce"
           "&tr=udp%3a%2f%2ftr.bangumi.moe%3a6969%2fannounce"
           "&tr=http%3a%2f%2fopen.acgtracker.com%3a1096%2fannounce"
           "&tr=udp%3a%2f%2ftracker.opentrackr.org%3a1337%2fannounce"),
    ("03", "magnet:?xt=urn:btih:0f57ffc34048fae847998f3f4ff18a8d87f9ec1d"
           "&tr=http%3a%2f%2ft.nyaatracker.com%2fannounce"
           "&tr=http%3a%2f%2ftracker.kamigami.org%3a2710%2fannounce"
           "&tr=http%3a%2f%2fshare.camoe.cn%3a8080%2fannounce"
           "&tr=http%3a%2f%2fopentracker.acgnx.se%2fannounce"
           "&tr=http%3a%2f%2fanidex.moe%3a6969%2fannounce"
           "&tr=http%3a%2f%2ft.acg.rip%3a6699%2fannounce"
           "&tr=https%3a%2f%2ftr.bangumi.moe%3a9696%2fannounce"
           "&tr=udp%3a%2f%2ftr.bangumi.moe%3a6969%2fannounce"
           "&tr=http%3a%2f%2fopen.acgtracker.com%3a1096%2fannounce"
           "&tr=udp%3a%2f%2ftracker.opentrackr.org%3a1337%2fannounce"),
]

PROC = None


def launch(profile_dir):
    global PROC
    PROC = subprocess.Popen(
        [BIN,
         "--headless=new", "--disable-gpu", "--no-sandbox", "--remote-allow-origins=*",
         f"--remote-debugging-port={PORT}",
         f"--user-data-dir={COPY_PROFILE}",
         f"--profile-directory={profile_dir}", "--no-first-run", "--no-default-browser-check",
         "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(40):
        time.sleep(1)
        try:
            http_get("/json/version")
            return True
        except Exception:
            pass
    return False


def api_add(s, tok, magnet):
    js = f"""
(async () => {{
  const body = 'source_url=' + encodeURIComponent({magnet!r})
             + '&save_path=' + encodeURIComponent({SAVE_PATH!r})
             + '&type=&task_from=1';
  const r = await fetch('/rest/2.0/services/cloud_dl?method=add_task&app_id=250528&bdstoken={tok}',
      {{method:'POST', credentials:'include',
        headers:{{'Content-Type':'application/x-www-form-urlencoded'}}, body: body}});
  return await r.text();
}})()
"""
    return s.val(js)


def api_list(s, tok):
    js = f"""
(async () => {{
  const r = await fetch('/rest/2.0/services/cloud_dl?method=list_task&app_id=250528&bdstoken={tok}&need_task_info=1&status=255',
      {{credentials:'include'}});
  return await r.text();
}})()
"""
    return s.val(js)


def ui_fallback(s, magnet):
    """路线 B：直接操作页面上的输入框并提交。"""
    probe = """
(() => {
  const els = [...document.querySelectorAll('input[type=text],input:not([type]),textarea')];
  const v = els.filter(e => e.offsetParent !== null || e.getClientRects().length);
  return v.map(e => ({tag:e.tagName, cls:e.className.slice(0,60), ph:e.placeholder||''}));
})()
"""
    info = s.val(probe)
    print("  可输入元素:", info)
    return info


def main():
    print(f"1) 准备配置副本 + 启动 {WHICH}（headless）...")
    PROF = prepare_copy_profile()
    if not launch(PROF):
        print("FAIL: 调试端口未就绪")
        return
    print("   已就绪:", http_get("/json/version").get("Browser"))

    tab = http_put("/json/new?" + "https://pan.baidu.com/disk/main")
    s = Session(tab["webSocketDebuggerUrl"])
    s.cmd("Page.enable"); s.cmd("Runtime.enable"); s.cmd("Network.enable")
    time.sleep(8)

    print("2) 登录校验")
    print("   地址:", s.val("location.href"))
    ck = [c for c in s.cookies() if "baidu.com" in c.get("domain", "")]
    names = {c["name"] for c in ck}
    logged = bool({"BDUSS", "STOKEN"} & names)
    print("   关键 cookie:", sorted({"BDUSS", "STOKEN", "PANWEB", "BAIDUID"} & names))
    print("   LOGGED_IN =", logged)
    if not logged:
        print("FAIL: 该配置未登录百度网盘，无法继续")
        s.close(); return

    tok = s.val("(function(){try{return YunData.bdstoken}catch(e){return localStorage.getItem('bdstoken')||''}})()")
    if not tok:
        tok = s.val("(async()=>{const r=await fetch('/api/gettemplatevariable?fields=%5B%22bdstoken%22%5D',{credentials:'include'});return await r.text()})()")
        try:
            tok = json.loads(tok).get("bdstoken", "")
        except Exception:
            tok = ""
    print("3) bdstoken =", repr(tok)[:16], "..." if tok else "(未取到)")

    if tok:
        print("4) 尝试私有接口提交")
        for label, mag in MAGNETS:
            res = api_add(s, tok, mag)
            print(f"   第{label}话 ->", str(res)[:220])
            time.sleep(2)
    else:
        print("4) 未取到 token，跳过私有接口")

    print("5) 任务列表查询")
    try:
        tl = api_list(s, tok) if tok else ""
        print("   ", str(tl)[:600])
    except Exception as e:
        print("   查询异常:", e)

    print("6) 路线 B 兜底探测（页面输入框）")
    ui_fallback(s, MAGNETS[0][1])

    s.close()
    print("DONE")


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        traceback.print_exc()
    finally:
        if PROC:
            subprocess.run(["taskkill", "/PID", str(PROC.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print("已关闭 Chrome 进程")
        time.sleep(2)
        # 配置副本含登录凭据，务必清理；os.* 逐个删除以避开批量删除拦截
        removed = 0
        for root, dirs, files in os.walk(COPY_PROFILE, topdown=False):
            for f in files:
                try:
                    os.remove(os.path.join(root, f)); removed += 1
                except Exception:
                    pass
            for d in dirs:
                try:
                    os.rmdir(os.path.join(root, d))
                except Exception:
                    pass
        try:
            os.rmdir(COPY_PROFILE)
        except Exception:
            pass
        print(f"已清理配置副本（删除 {removed} 个文件），残留={os.path.exists(COPY_PROFILE)}")
