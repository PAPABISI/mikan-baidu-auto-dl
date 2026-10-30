# -*- coding: utf-8 -*-
"""配置驱动追番流水线。CLI 与 Web 共用；提交记录先落盘再发请求。"""
import datetime as dt
import json
import os
from decimal import Decimal

from baidu import BaiduClient, LoginRequired, NETWORK_ERRORS, errno, make_opener, req, safe_error
from filelock import FileLock
from parse_mikan import parse_mikan, select_best, select_for_series
from storage import BASE, load_list, mutate_list, read_json, write_json

RUN_LOG = os.path.join(BASE, ".auto_dl_state.json")
RUN_LOCK = os.path.join(BASE, ".run.lock")
HISTORY_FILE = os.path.join(BASE, ".task_history.json")
PROXY_WEB = os.environ.get("MIKAN_PROXY", "http://127.0.0.1:7897")
PREF_KEYS = ("subtitle_groups", "language", "embedded_only", "save_path")
TASK_STATUS = {
    "0": "下载完成", "1": "下载中", "2": "系统错误", "3": "资源不存在",
    "4": "下载超时", "5": "下载失败（可能被网盘过滤）", "6": "存储空间不足",
    "7": "目标已存在，需人工核验", "8": "任务取消",
}


def fetch_mikan(mikan_id):
    op, _ = make_opener(proxy=PROXY_WEB)
    return req(op, f"https://mikanani.me/Home/Bangumi/{mikan_id}",
               referer="https://mikanani.me/", timeout=30).decode("utf-8", "replace")


def today():
    return dt.date.today().isoformat()


def emit(status, msg="", **extra):
    state = {"status": status, "msg": msg, "date": today(),
             "time": dt.datetime.now().isoformat(timespec="seconds"), **extra}
    write_json(RUN_LOG, state)
    print(f"[{status}] {msg}", flush=True)
    return state


def task_key(series, ep):
    return f"{series['mikan_id']}|{series['save_path']}|{Decimal(ep).normalize()}"


def retry_failed(mikan_id):
    with FileLock(RUN_LOCK, timeout=0):
        history = read_json(HISTORY_FILE, {})
        keys = [key for key, record in history.items()
                if record["mikan_id"] == mikan_id and record["state"] in ("FAILED", "REJECTED")]
        for key in keys:
            del history[key]
        write_json(HISTORY_FILE, history)
        return len(keys)


def resolve_unknown(key, action):
    if action not in ("completed", "retry"):
        raise ValueError("不支持的处理方式")
    with FileLock(RUN_LOCK, timeout=0):
        history = read_json(HISTORY_FILE, {})
        record = history.get(key)
        if not record or record["state"] != "UNKNOWN":
            raise ValueError("这项任务已变化，请刷新列表")
        if action == "completed":
            record.update(state="COMPLETED", detail="用户已核验入盘")
        else:
            del history[key]
        write_json(HISTORY_FILE, history)


def update_task(record, info):
    if not info or str(info.get("result", 0)) != "0":
        record.update(state="UNKNOWN", detail="未查到任务，请核验网盘；为避免重复，暂停自动重提")
        return
    status = str(info.get("status", ""))
    names = [info.get("task_name") or ""] + [
        item.get("file_name", "") for item in info.get("file_list", [])]
    record.update(task_status=status, task_name=info.get("task_name"),
                  detail=TASK_STATUS.get(status, f"未知任务状态 {status}"))
    if any(name.lower().endswith(".bak") for name in names):
        record.update(state="FAILED", detail="网盘返回 .bak 文件，可能被过滤，请核验")
    elif status == "0":
        record["state"] = "COMPLETED"
    elif status in ("2", "3", "4", "5", "6", "7", "8"):
        record["state"] = "FAILED"
    else:
        record["state"] = "PENDING"


def _current_series(snapshot):
    current = next((s for s in load_list()["series"] if s["mikan_id"] == snapshot["mikan_id"]), None)
    if current is None or any(current.get(k) != snapshot.get(k) for k in PREF_KEYS):
        raise ValueError("追番配置已被修改或删除，请重新检查")
    return current


def _persist_series(snapshot, best, history, status, detail):
    def merge(cfg):
        current = next((s for s in cfg["series"] if s["mikan_id"] == snapshot["mikan_id"]), None)
        if current is None or any(current.get(k) != snapshot.get(k) for k in PREF_KEYS):
            return
        cursor = Decimal(str(current.get("last_episode", "0")))
        # 只越过已确认完成的资源；前面一话失败不能被后面的成功覆盖。
        episodes = {Decimal(item["ep"]) for item in best}
        episodes.update(Decimal(record["ep"]) for record in history.values()
                        if record["mikan_id"] == snapshot["mikan_id"]
                        and record["save_path"] == snapshot["save_path"])
        for ep in sorted(episodes):
            if ep <= cursor:
                continue
            if history.get(task_key(snapshot, ep), {}).get("state") != "COMPLETED":
                break
            cursor = ep
        if cursor > Decimal(str(current.get("last_episode", "0"))):
            current["last_episode"] = str(cursor)
        current.update(last_check=today(), last_check_status=status, last_check_detail=detail)
    mutate_list(merge)


def _run(client, on_progress):
    cfg = load_list()
    history = read_json(HISTORY_FILE, {})
    if not isinstance(history, dict):
        raise ValueError("任务记录格式错误，请先检查 .task_history.json")
    active_ids = {s["mikan_id"] for s in cfg["series"]}
    tracked = [record for record in history.values()
               if record["mikan_id"] in active_ids and record["state"] in ("PENDING", "UNKNOWN")
               and record.get("task_id")]
    query_error = None
    if tracked:
        try:
            info = client.query_tasks([r["task_id"] for r in tracked])
            for record in tracked:
                update_task(record, info.get(str(record["task_id"])))
            write_json(HISTORY_FILE, history)
        except LoginRequired:
            raise
        except Exception as error:
            query_error = safe_error(error)

    rows, errors = [], []
    for series in cfg["series"]:
        best = []
        emit("RUNNING", f"正在检查 {series['name']}", submitted=rows)
        try:
            items = parse_mikan(fetch_mikan(series["mikan_id"]))
            if not items:
                raise ValueError("详情页未解析到资源，请检查代理、页面内容或解析器")
            best = select_for_series(items, series)
            newer = [it for it in best if Decimal(it["ep"]) > Decimal(str(series.get("last_episode", "0")))]
            for item in newer:
                _current_series(series)
                key = task_key(series, item["ep"])
                record = history.get(key)
                if record is None or (record["state"] == "REJECTED" and record.get("attempts", 0) < 3):
                    # UNKNOWN 必须先写入；进程中断或响应丢失时，下次不会盲目重复提交。
                    record = {
                        "mikan_id": series["mikan_id"], "series": series["name"], "ep": item["ep"],
                        "name": f"{series['name']} 第{item['ep']}话", "save_path": series["save_path"],
                        "infohash": item["infohash"], "state": "UNKNOWN",
                        "detail": "正在提交，结果尚未确认", "time": dt.datetime.now().isoformat(),
                        "attempts": (record or {}).get("attempts", 0) + 1,
                    }
                    history[key] = record
                    write_json(HISTORY_FILE, history)
                    try:
                        result = client.add_task(item["magnet"], series["save_path"])
                        code = errno(result)
                        tid = result.get("task_id")
                        record["errno"] = code
                        if str(code) == "-6":
                            record.update(state="REJECTED", detail="登录态失效")
                            raise LoginRequired("登录态失效，请重新扫码")
                        if tid and (code is None or str(code) == "0"):
                            record.update(state="PENDING", task_id=str(tid), detail="已受理，等待网盘回读")
                        elif code is not None and str(code) != "0":
                            record.update(state="REJECTED", detail=f"提交被拒绝，错误码 {code}；最多跨运行重试 3 次")
                        else:
                            record.update(state="UNKNOWN", detail="响应未包含任务 ID，需人工核验网盘")
                    except LoginRequired:
                        write_json(HISTORY_FILE, history)
                        raise
                    except Exception as error:
                        record.update(state="UNKNOWN", detail="提交结果不确定，请核验网盘：" + safe_error(error))
                    write_json(HISTORY_FILE, history)
                if on_progress:
                    on_progress(record.copy())
                progress = [r.copy() for r in history.values()
                            if r["mikan_id"] == series["mikan_id"] and r["save_path"] == series["save_path"]
                            and Decimal(r["ep"]) > Decimal(str(series.get("last_episode", "0")))]
                emit("RUNNING", f"正在处理 {series['name']} 第{item['ep']}话", submitted=rows + progress)

            records = sorted((record for record in history.values()
                              if record["mikan_id"] == series["mikan_id"]
                              and record["save_path"] == series["save_path"]
                              and Decimal(record["ep"]) > Decimal(str(series.get("last_episode", "0")))),
                             key=lambda record: Decimal(record["ep"]))
            pending = [r for r in records if r["state"] == "PENDING" and r.get("task_id")]
            if pending:
                try:
                    info = client.query_tasks([r["task_id"] for r in pending])
                    for record in pending:
                        update_task(record, info.get(str(record["task_id"])))
                except LoginRequired:
                    raise
                except Exception as error:
                    query_error = safe_error(error)
                    for record in pending:
                        record["detail"] = "已受理，但任务回读失败：" + query_error
                write_json(HISTORY_FILE, history)
            rows.extend(r.copy() for r in records)
            states = {r["state"] for r in records}
            status = ("GENERIC" if states & {"REJECTED", "FAILED"} else
                      "UNKNOWN" if "UNKNOWN" in states else
                      "PENDING" if "PENDING" in states else
                      "OK" if records else "NO_UPDATES")
            detail = f"匹配 {len(best)} 话，本次跟踪 {len(records)} 话"
            _persist_series(series, best, history, status, detail)
        except LoginRequired:
            raise
        except Exception as error:
            status = "NET_ERROR" if isinstance(error, NETWORK_ERRORS) else "GENERIC"
            detail = safe_error(error)
            errors.append({"series": series["name"], "status": status, "msg": detail})
            _persist_series(series, [], history, status, detail)
    if query_error:
        errors.append({"status": "NET_ERROR", "msg": "任务回读失败：" + query_error})
    states = {r["state"] for r in rows}
    status = ("NET_ERROR" if errors and all(e["status"] == "NET_ERROR" for e in errors) else
              "GENERIC" if errors or states & {"FAILED", "REJECTED"} else
              "UNKNOWN" if "UNKNOWN" in states else
              "PENDING" if "PENDING" in states else "OK" if rows else "NO_UPDATES")
    complete = sum(r["state"] == "COMPLETED" for r in rows)
    pending = sum(r["state"] == "PENDING" for r in rows)
    msg = ("无新话" if status == "NO_UPDATES" else
           f"完成 {complete} 话，等待网盘 {pending} 话，需处理 {len(rows) - complete - pending} 话")
    if errors:
        msg += f"；{len(errors)} 项检查失败"
    return emit(status, msg, submitted=rows, errors=errors)


def run(on_progress=None, client=None):
    try:
        lock = FileLock(RUN_LOCK, timeout=0)
        lock.__enter__()
    except TimeoutError:
        return {"status": "BUSY", "msg": "已有检查在运行", "submitted": []}
    try:
        emit("RUNNING", "正在检查追番列表", submitted=[])
        # 空列表无需先登录。
        if not load_list()["series"]:
            return emit("NO_UPDATES", "追番列表为空", submitted=[])
        if client is None:
            client = BaiduClient()
            client.login_info()
        return _run(client, on_progress)
    except LoginRequired as error:
        return emit("QRCODE_NEEDED", str(error), submitted=[])
    except NETWORK_ERRORS as error:
        return emit("NET_ERROR", safe_error(error), submitted=[])
    except Exception as error:
        return emit("GENERIC", safe_error(error), submitted=[])
    finally:
        lock.__exit__(None, None, None)


def main():
    state = run()
    return 0 if state["status"] in ("OK", "NO_UPDATES", "PENDING") else 2


if __name__ == "__main__":
    raise SystemExit(main())
