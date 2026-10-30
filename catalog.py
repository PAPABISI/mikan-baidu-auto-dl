"""浏览器 Agent 工作队列和永久操作记录。此模块不向网盘发送下载请求。"""
import copy
import datetime as dt
import hashlib
import os
import re
import time
import uuid
from decimal import Decimal

import storage
from filelock import FileLock
from parse_mikan import resolution_of, subtitle_kind, select_for_series, matches_preferences

DATA_FILE = os.path.join(storage.BASE, "data", "library.json")
DATA_LOCK = os.path.join(storage.BASE, "data", ".library.lock")
ALL_STATES = {"READY", "CLAIMED", "SUBMITTING", "SUBMITTED", "DOWNLOADING", "COMPLETED", "FAILED", "UNKNOWN", "SUPERSEDED"}
TRANSITIONS = {
    "CLAIMED": {"SUBMITTING", "READY", "FAILED"},
    "SUBMITTING": {"SUBMITTED", "FAILED", "UNKNOWN"},
    "SUBMITTED": {"DOWNLOADING", "COMPLETED", "FAILED", "UNKNOWN"},
    "DOWNLOADING": {"COMPLETED", "FAILED", "UNKNOWN"},
    "UNKNOWN": {"COMPLETED", "FAILED"},
}


def now():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds")


def load():
    data = storage.read_json(DATA_FILE, {"version": 1, "metadata": {}, "jobs": {}, "events": [], "scheduler": {}})
    if data.get("version") != 1:
        raise ValueError("不支持的番剧库版本")
    return data


def transact(callback):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with FileLock(DATA_LOCK, timeout=15):
        data = load()
        result = callback(data)
        storage.write_json(DATA_FILE, data)
        return result


def event(data, kind, message, mikan_id=None, job_id=None, **extra):
    record = {"id": uuid.uuid4().hex, "at": now(), "kind": kind, "message": message,
              "mikan_id": mikan_id, "job_id": job_id, **extra}
    data["events"].append(record)
    return record


def save_metadata(mikan_id, values):
    allowed = {"bangumi_id", "name_original", "name_cn", "summary", "cover_url", "total_episodes", "air_date",
               "broadcast_weekday", "broadcast_source", "broadcast_note", "broadcast_time", "broadcast_timezone",
               "release_weekdays", "release_note", "release_samples", "score", "studio", "official_url",
               "metadata_updated_at", "last_scan", "last_scan_error", "available_episodes", "folder_verified_at",
               "score_votes", "rank", "platform"}
    if not isinstance(values, dict):
        raise ValueError("番剧资料必须是对象")
    if any(key not in allowed for key in values):
        raise ValueError("不支持的资料字段")
    day = values.get("broadcast_weekday")
    if day is not None and (type(day) is not int or day not in range(1, 8)):
        raise ValueError("放送星期必须为 1 到 7")
    def update(data):
        meta = data["metadata"].setdefault(str(mikan_id), {})
        meta.update(values)
        event(data, "METADATA", "更新番剧资料", mikan_id)
        return copy.deepcopy(meta)
    return transact(update)


def target_path(name):
    clean = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .")
    if not clean:
        raise ValueError("番名不能为空")
    return "/Anime/" + clean


def check_target(path):
    if (not isinstance(path, str) or not path.startswith("/Anime/") or not path[7:].strip()
            or any(not part.strip(" .") for part in path[1:].split("/"))
            or re.search(r'[\\:*?"<>|\x00-\x1f]', path)):
        raise ValueError("浏览器添加的保存路径必须位于 /Anime/番名")


def clean_evidence(value):
    if not isinstance(value, str) or len(value.strip()) < 4:
        raise ValueError("请提供观察到的具体证据")
    return re.sub(r"(?i)(BDUSS|STOKEN|PTOKEN|bdstoken)\s*[:=]\s*[^\s;,]+", r"\1=[redacted]", value[:2000])


def watch_series(mikan_id, name, subtitle_groups=None, **fields):
    item = {"mikan_id": mikan_id, "name": name, "subtitle_groups": subtitle_groups or [],
            "language": "简体", "embedded_only": True, "save_path": target_path(name),
            "last_episode": "0", **fields}
    if "subtitle_type" in fields:
        item["embedded_only"] = fields["subtitle_type"] == "内嵌"
    storage.validate_series(item)
    check_target(item["save_path"])
    def update(cfg):
        existing = next((s for s in cfg["series"] if s["mikan_id"] == mikan_id), None)
        if existing:
            # 保留旧游标作为历史配置，不把它当作新目录已入盘的证据。
            item["last_episode"] = existing.get("last_episode", "0")
            existing.update(item)
        else:
            cfg["series"].append(item)
    storage.mutate_list(update)
    transact(lambda data: event(data, "WATCH", "追番保存到 " + item["save_path"], mikan_id))
    return item


def enqueue(series, items):
    check_target(series["save_path"])
    def update(data):
        added = []
        for job in data["jobs"].values():
            if job["mikan_id"] != series["mikan_id"] or job["state"] != "READY":
                continue
            candidate = {"ep": job["episode"], "ver": job.get("version", 1), "group": job["subtitle_group"],
                         "lang": job["language"], "embedded": job["embedded"], "title": job["title"],
                         "soft": job.get("subtitle_type") in ("外挂", "内封") or bool(re.search(r"外挂|外掛|字幕文件|軟字幕|软字幕|内封|內封", job["title"])),
                         "sealed": job.get("subtitle_type") == "内封" or bool(re.search(r"内封|內封", job["title"])),
                         "resolution": job.get("resolution", resolution_of(job["title"])), "infohash": job["infohash"]}
            if job["save_path"] != series["save_path"] or not select_for_series([candidate], series):
                job.update(state="SUPERSEDED", updated_at=now())
                event(data, "SUPERSEDED", "追番偏好已变更，旧待添加资源不再匹配；未提交网盘", series["mikan_id"], job["id"])
        for item in items:
            if item.get("is_collection") or item.get("ep") == "?":
                continue
            same = [j for j in data["jobs"].values() if j["mikan_id"] == series["mikan_id"]
                    and j["save_path"] == series["save_path"] and Decimal(j["episode"]) == Decimal(item["ep"])]
            # 已提交、结果不确定或正在处理的话数都不能换个 hash 自动重提。
            if any(j["state"] not in ("READY", "FAILED", "SUPERSEDED") for j in same):
                continue
            identity = f"{series['mikan_id']}|{series['save_path']}|{item['infohash']}"
            jid = hashlib.sha256(identity.encode()).hexdigest()[:24]
            existing = data["jobs"].get(jid)
            if existing and existing["state"] != "SUPERSEDED":
                continue
            for old in same:
                if old["state"] == "READY":
                    old.update(state="SUPERSEDED", updated_at=now())
                    event(data, "SUPERSEDED", "已由更合适的资源替代", series["mikan_id"], old["id"])
            if existing:
                existing.update(state="READY", updated_at=now())
                existing.pop("claim_token", None)
                existing.pop("lease_until", None)
                event(data, "READY", "偏好重新匹配，恢复该话待添加资源", series["mikan_id"], jid)
                added.append(copy.deepcopy(existing))
                continue
            job = {"id": jid, "mikan_id": series["mikan_id"], "series_name": series["name"],
                   "episode": item["ep"], "version": item.get("ver", 1), "title": item["title"],
                   "subtitle_group": item["group"], "language": item.get("lang"),
                   "embedded": bool(item.get("embedded")), "size": item.get("size"),
                   "subtitle_type": subtitle_kind(item), "resolution": item.get("resolution", resolution_of(item["title"])),
                   "release_date": item.get("date"), "infohash": item["infohash"],
                   "magnet": "magnet:?xt=urn:btih:" + item["infohash"], "save_path": series["save_path"],
                   "source_url": f"https://mikanani.me/Home/Bangumi/{series['mikan_id']}",
                   "state": "READY", "created_at": now(), "updated_at": now(), "attempts": []}
            data["jobs"][jid] = job
            event(data, "READY", f"发现第 {job['episode']} 话 · {job['subtitle_group']}", series["mikan_id"], jid)
            added.append(copy.deepcopy(job))
        return added
    return transact(update)


def discover_collections(series, items):
    """保存合集候选；不建立逐话任务，不推进进度，也不进入自动领取队列。"""
    check_target(series["save_path"])
    def update(data):
        records = data.setdefault("collections", {})
        added = []
        for item in items:
            if not item.get("is_collection"):
                continue
            identity = f"{series['mikan_id']}|{series['save_path']}|{item['infohash']}"
            cid = "c-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
            values = {key: copy.deepcopy(item.get(key)) for key in (
                "title", "magnet", "infohash", "group", "lang", "embedded", "soft", "sealed", "resolution",
                "is_collection", "episode_start", "episode_end", "episode_label", "size", "date", "torrent")}
            values.update(id=cid, mikan_id=series["mikan_id"], series_name=series["name"], save_path=series["save_path"],
                          subtitle_type=subtitle_kind(item),
                          source_url=f"https://mikanani.me/Home/Bangumi/{series['mikan_id']}", last_seen=now())
            if cid not in records:
                records[cid] = {**values, "first_seen": now()}
                event(data, "COLLECTION", "发现" + item["episode_label"] + " · " + item["group"] + "；待人工核对，不自动入队",
                      series["mikan_id"], collection_id=cid)
                added.append(copy.deepcopy(records[cid]))
            else:
                records[cid].update(values)
        return added
    return transact(update)


def expire_claims(data):
    for job in data["jobs"].values():
        if job["state"] in ("CLAIMED", "SUBMITTING") and job.get("lease_until", float("inf")) < time.time():
            job["state"] = "READY" if job["state"] == "CLAIMED" else "UNKNOWN"
            job["updated_at"] = now()
            job.pop("claim_token", None)
            event(data, job["state"], "Agent 租约到期；已开始的提交需先核验，不能盲目重试", job["mikan_id"], job["id"])


def claim(owner, job_id=None):
    if not isinstance(owner, str) or not owner.strip() or len(owner) > 100:
        raise ValueError("请提供 Agent 名称")
    def update(data):
        if data.get("executor", {}).get("status") == "BLOCKED":
            raise ValueError("网盘自动执行受阻，请先处理页面提示；当前仅准备资源")
        expire_claims(data)
        jobs = sorted(data["jobs"].values(), key=lambda j: (j["mikan_id"], Decimal(j["episode"]), j["created_at"]))
        job = next((j for j in jobs if j["state"] == "READY" and (job_id is None or j["id"] == job_id)), None)
        if job is None:
            return None
        job.update(state="CLAIMED", owner=owner.strip(), claim_token=uuid.uuid4().hex,
                   lease_until=time.time() + 900, updated_at=now())
        job["attempts"].append({"at": now(), "owner": owner.strip()})
        event(data, "CLAIMED", "Agent 已领取，尚未点击网盘提交", job["mikan_id"], job["id"], owner=owner.strip())
        return copy.deepcopy(job)
    return transact(update)


def report(job_id, token, state, evidence, remote_task_id=None):
    if state not in ALL_STATES or not isinstance(evidence, str) or len(evidence.strip()) < 4:
        raise ValueError("请提供合法状态及观察到的网页证据")
    evidence = clean_evidence(evidence)
    def update(data):
        expire_claims(data)
        job = data["jobs"].get(job_id)
        if job is None:
            raise ValueError("任务不存在")
        if not token or job.get("claim_token") != token:
            raise ValueError("领取令牌失效，请重新读取任务并核验网盘")
        if job["state"] == state and job.get("evidence") == evidence:
            return public_job(job)
        if state not in TRANSITIONS.get(job["state"], set()):
            raise ValueError(f"不允许从 {job['state']} 变为 {state}")
        job.update(state=state, evidence=evidence, updated_at=now(), lease_until=time.time() + 900)
        if remote_task_id is not None:
            job["remote_task_id"] = str(remote_task_id)
        if state == "COMPLETED":
            job["completed_at"] = now()
        if state == "SUBMITTED":
            job["submitted_at"] = now()
        event(data, state, evidence, job["mikan_id"], job_id, owner=job.get("owner"))
        return public_job(job)
    return transact(update)


def review(job_id, state, evidence):
    if state not in ("COMPLETED", "FAILED", "DOWNLOADING", "UNKNOWN") or not isinstance(evidence, str) or len(evidence.strip()) < 4:
        raise ValueError("核验状态或证据无效")
    evidence = clean_evidence(evidence)
    def update(data):
        job = data["jobs"].get(job_id)
        if not job or job["state"] not in ("SUBMITTED", "DOWNLOADING", "UNKNOWN"):
            raise ValueError("任务当前不需要核验")
        job.update(state=state, evidence=evidence[:2000], updated_at=now())
        if state == "COMPLETED":
            job["completed_at"] = now()
        event(data, state, evidence[:2000], job["mikan_id"], job_id, owner="review")
        return public_job(job)
    return transact(update)


def observe(job_id, state, evidence, source):
    """记录人工添加或已存在文件的证据；不声称执行过自动提交。"""
    if state not in ("SUBMITTED", "DOWNLOADING", "COMPLETED", "FAILED") or source not in ("user", "web"):
        raise ValueError("观察状态或来源无效")
    evidence = clean_evidence(evidence)
    def update(data):
        job = data["jobs"].get(job_id)
        return _record_observation(data, job, state, evidence, source)
    return transact(update)


def _record_observation(data, job, state, evidence, source):
    if not job or job["state"] in ("CLAIMED", "SUBMITTING", "SUPERSEDED", "COMPLETED"):
        raise ValueError("任务不存在、正在执行、已入盘或已替换，不能覆盖观察结果")
    job.update(state=state, evidence=evidence, observation_source=source, updated_at=now())
    if state == "COMPLETED":
        job["completed_at"] = now()
    event(data, state, ("用户确认：" if source == "user" else "网页核验：") + evidence,
          job["mikan_id"], job["id"], source=source)
    return public_job(job)


def observe_batch(job_ids, state, evidence, source):
    """原子记录同一番剧目录的多话观察结果，每话保留独立证据和历史。"""
    if (not isinstance(job_ids, list) or not job_ids
            or any(not isinstance(value, str) or not value for value in job_ids)
            or len(set(job_ids)) != len(job_ids)):
        raise ValueError("请选择不重复的任务编号列表")
    if state not in ("SUBMITTED", "DOWNLOADING", "COMPLETED", "FAILED") or source not in ("user", "web"):
        raise ValueError("观察状态或来源无效")
    evidence = clean_evidence(evidence)
    def update(data):
        jobs = [data["jobs"].get(job_id) for job_id in job_ids]
        if any(not job for job in jobs):
            raise ValueError("部分任务已不存在，请刷新后重新选择；本批未保存")
        if len({(job["mikan_id"], job["save_path"]) for job in jobs}) != 1:
            raise ValueError("批量记录仅支持同一番剧和保存目录")
        results = []
        for job in jobs:
            context = f"{job['series_name']} · 第 {job['episode']} 话 · {job['save_path']}；{evidence}"
            results.append(_record_observation(data, job, state, clean_evidence(context), source))
        return results
    return transact(update)


def public_job(job):
    result = {key: copy.deepcopy(value) for key, value in job.items() if key not in ("claim_token", "lease_until")}
    result.setdefault("resolution", resolution_of(job["title"]))
    return result


def retry(job_id, evidence):
    if not isinstance(evidence, str) or len(evidence.strip()) < 4:
        raise ValueError("重试前请核验网盘并提供说明")
    evidence = clean_evidence(evidence)
    def update(data):
        job = data["jobs"].get(job_id)
        if not job or job["state"] != "FAILED":
            raise ValueError("仅明确失败的任务可以重试；不确定的结果必须先核验")
        job.update(state="READY", updated_at=now())
        job.pop("claim_token", None)
        event(data, "READY", evidence[:2000], job["mikan_id"], job_id)
        return public_job(job)
    return transact(update)


def snapshot(include_all_events=False):
    data = load()
    jobs = [public_job(j) for j in data["jobs"].values()]
    collections = list(data.get("collections", {}).values())
    series = []
    for config in storage.load_list()["series"]:
        related = [j for j in jobs if j["mikan_id"] == config["mikan_id"] and j["save_path"] == config["save_path"]]
        stats = {state: sum(j["state"] == state for j in related) for state in ALL_STATES}
        batches = []
        for candidate in collections:
            if candidate["mikan_id"] != config["mikan_id"] or candidate["save_path"] != config["save_path"]:
                continue
            start, end = candidate.get("episode_start"), candidate.get("episode_end")
            overlap = sorted({j["episode"] for j in related
                              if start is not None and end is not None and start <= Decimal(j["episode"]) <= end
                              and j["state"] in ("CLAIMED", "SUBMITTING", "SUBMITTED", "DOWNLOADING", "UNKNOWN", "COMPLETED")}, key=Decimal)
            batches.append({**candidate, "matches_preferences": matches_preferences(candidate, config), "overlap_episodes": overlap})
        series.append({**config, **data["metadata"].get(str(config["mikan_id"]), {}),
                       "stats": stats, "jobs": sorted(related, key=lambda j: Decimal(j["episode"])), "collections": batches})
    return {"version": 1, "mode": "browser-agent", "series": series, "jobs": jobs,
            "collections": collections,
            "events": list(reversed(data["events"] if include_all_events else data["events"][-200:])),
            "event_count": len(data["events"]), "scheduler": data.get("scheduler", {}),
            "executor": data.get("executor", {}),
            "stats": {state: sum(j["state"] == state for j in jobs) for state in ALL_STATES},
            "updated_at": now(), "baidu_url": "https://pan.baidu.com/disk/main"}
