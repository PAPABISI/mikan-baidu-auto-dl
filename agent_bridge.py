"""确定性的资源准备阶段：只访问蜜柑/Bangumi，不提交网盘任务。"""
import datetime as dt
import os

import auto_dl
import catalog
import metadata
import storage
from baidu import safe_error
from filelock import FileLock
from parse_mikan import parse_mikan, select_for_series

PREPARE_LOCK = os.path.join(storage.BASE, ".prepare.lock")


def prepare(mikan_id=None):
    try:
        lock = FileLock(PREPARE_LOCK, timeout=0)
        lock.__enter__()
    except TimeoutError:
        return {"status": "BUSY", "msg": "已有蜜柑检查在运行"}
    errors, added = [], []
    try:
        auto_dl.emit("RUNNING", "正在准备浏览器添加队列，不会自动点击网盘提交")
        series = storage.load_list()["series"]
        if mikan_id is not None:
            series = [s for s in series if s["mikan_id"] == mikan_id]
            if not series:
                raise ValueError("未找到追番项")
        for item in series:
            auto_dl.emit("RUNNING", "正在检查 " + item["name"])
            try:
                resources = parse_mikan(auto_dl.fetch_mikan(item["mikan_id"]))
                if not resources:
                    raise ValueError("未解析到资源，请检查蜜柑页面或代理")
                selected = select_for_series(resources, item)
                new = catalog.enqueue(item, selected)
                added.extend(new)
                values = {"last_scan": catalog.now(), "last_scan_error": None,
                          "available_episodes": len(selected), **metadata.release_pattern(selected)}
                catalog.save_metadata(item["mikan_id"], values)
                meta = catalog.load()["metadata"].get(str(item["mikan_id"]), {})
                if meta.get("bangumi_id"):
                    stamp = meta.get("metadata_updated_at")
                    expected_source = f"https://bgm.tv/subject/{meta['bangumi_id']}"
                    stale = (meta.get("broadcast_source") != expected_source or not stamp
                             or dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(stamp) > dt.timedelta(days=7))
                    if stale:
                        try:
                            fresh = metadata.get_subject(meta["bangumi_id"])
                            catalog.save_metadata(item["mikan_id"], fresh)
                        except Exception as error:
                            # 资料站不可用不能挡住已解析到的资源，也不能假装资料更新成功。
                            errors.append({"series": item["name"], "msg": "Bangumi 资料更新失败：" + safe_error(error)})
            except Exception as error:
                message = safe_error(error)
                catalog.save_metadata(item["mikan_id"], {"last_scan": catalog.now(), "last_scan_error": message})
                errors.append({"series": item["name"], "msg": message})
        ready = sum(job["state"] == "READY" for job in catalog.load()["jobs"].values())
        status = "GENERIC" if errors else "READY" if ready else "NO_UPDATES"
        return auto_dl.emit(status, f"新增 {len(added)} 话，待 Agent 添加 {ready} 话", added=[j["id"] for j in added], errors=errors)
    except Exception as error:
        return auto_dl.emit("GENERIC", safe_error(error))
    finally:
        lock.__exit__(None, None, None)
