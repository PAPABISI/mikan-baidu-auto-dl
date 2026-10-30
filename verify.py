"""网盘目录核验：把网盘文件清单与本地任务匹配，用真实文件证据记录入盘。

清单由具备网盘只读能力的调用方（例如百度网盘连接器的目录浏览工具）提供；
本模块不访问网络，也不提交任何网盘任务。只有话数唯一对应、且任务允许被
观察覆盖时才记录 COMPLETED；存在歧义或证据不足时只报告，不写入。
"""
from decimal import Decimal

import catalog
from parse_mikan import collection_info, ep_of

OBSERVABLE_STATES = ("READY", "SUBMITTED", "DOWNLOADING", "UNKNOWN", "FAILED")
MAX_FILES = 1000


def _size_text(size):
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        return ""
    mb = size / 1048576
    return f"{mb:.1f} MB" if mb < 1024 else f"{mb / 1024:.2f} GB"


def evidence_for(save_path, filename, size=None):
    text = _size_text(size)
    detail = f"（{text}）" if text else ""
    return f"网盘连接器核验：{save_path}/{filename}{detail} 实际存在，已核实入盘"


def _normalize(files):
    if not isinstance(files, list) or not files or len(files) > MAX_FILES:
        raise ValueError(f"请提供 1 到 {MAX_FILES} 项的网盘文件清单")
    normalized = []
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("文件条目必须是对象")
        name = item.get("filename")
        if (not isinstance(name, str) or not name.strip()
                or any(char in name for char in '/\\\x00\r\n') or ".." in name
                or name.strip() in (".", "..")):
            raise ValueError("文件名无效：" + repr(name)[:80])
        size = item.get("size")
        if size is not None and (not isinstance(size, int) or isinstance(size, bool) or size < 0):
            raise ValueError("文件大小必须是非负整数")
        isdir = item.get("isdir", False)
        if not isinstance(isdir, bool):
            raise ValueError("isdir 必须是布尔值")
        normalized.append({"filename": name.strip(), "size": size, "isdir": isdir})
    return normalized


def verify_listing(save_path, files, source="web", apply=True):
    """用一份网盘目录文件清单核验该目录下的任务并记录入盘证据。

    source=web 表示调用方（Agent）通过授权接口亲自观察到网盘文件；source=user
    仅在用户明确提供清单并确认时使用。apply=False 只生成计划，不写入。
    """
    catalog.check_target(save_path)
    if source not in ("user", "web"):
        raise ValueError("观察来源必须是 user 或 web")
    normalized = _normalize(files)

    def update(data):
        observable = [job for job in data["jobs"].values()
                      if job["save_path"] == save_path and job["state"] in OBSERVABLE_STATES]
        files_by_ep, other_files = {}, []
        for entry in normalized:
            if entry["isdir"]:
                continue
            episode = ep_of(entry["filename"])
            if episode == "?" or collection_info(entry["filename"])["is_collection"]:
                # 合集与无法识别话数的文件不能对应到单话任务，仅如实报告。
                other_files.append(entry["filename"])
                continue
            files_by_ep.setdefault(Decimal(episode), []).append(entry)
        jobs_by_ep = {}
        for job in observable:
            jobs_by_ep.setdefault(Decimal(job["episode"]), []).append(job)
        matches, ambiguous = [], []
        for episode in sorted(set(files_by_ep) & set(jobs_by_ep)):
            entries, jobs = files_by_ep[episode], jobs_by_ep[episode]
            if len(entries) == 1 and len(jobs) == 1:
                matches.append((jobs[0], entries[0]))
            else:
                ambiguous.append({"episode": jobs[0]["episode"],
                                  "filenames": [entry["filename"] for entry in entries],
                                  "job_ids": [job["id"] for job in jobs]})
        recorded = []
        planned = []
        for job, entry in matches:
            planned.append({"job_id": job["id"], "episode": job["episode"], "state": job["state"],
                            "filename": entry["filename"], "size": entry["size"]})
            if apply:
                catalog._record_observation(data, job, "COMPLETED",
                                            evidence_for(save_path, entry["filename"], entry["size"]), source)
                recorded.append(catalog.public_job(job))
        return {
            "save_path": save_path, "source": source, "applied": apply,
            "recorded": recorded,
            "matched": planned,
            "ambiguous": ambiguous,
            "jobs_without_file": [{"job_id": job["id"], "episode": job["episode"], "state": job["state"]}
                                  for job in sorted(observable, key=lambda j: Decimal(j["episode"]))
                                  if Decimal(job["episode"]) not in files_by_ep],
            "files_without_job": other_files + [entry["filename"] for episode, entries in sorted(files_by_ep.items())
                                                if episode not in jobs_by_ep for entry in entries],
        }

    return catalog.transact(update)
