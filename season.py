"""Bangumi 季度动画分页浏览；只读取公开资料，缓存与真实追番库分开。"""
import copy
import datetime as dt
import os
import time

import metadata
import storage
from baidu import safe_error
from filelock import FileLock

CACHE_FILE = os.path.join(storage.BASE, "data", "season-cache.json")
CACHE_LOCK = os.path.join(storage.BASE, "data", ".season-cache.lock")
PAGE_SIZE = 20
CACHE_SECONDS = 1800


def current_season():
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    return today.year, (today.month - 1) // 3 + 1


def bounds(year, quarter):
    if type(year) is not int or year not in range(1900, 2101) or type(quarter) is not int or quarter not in range(1, 5):
        raise ValueError("年份须为1900–2100，季度须为1–4")
    month = (quarter - 1) * 3 + 1
    start = dt.date(year, month, 1)
    end = dt.date(year + 1, 1, 1) if quarter == 4 else dt.date(year, month + 3, 1)
    return start.isoformat(), end.isoformat()


def browse(year=None, quarter=None, page=1, sort="score", query="", refresh=False):
    default_year, default_quarter = current_season()
    year = default_year if year is None else year
    quarter = default_quarter if quarter is None else quarter
    start, end = bounds(year, quarter)
    if type(page) is not int or page < 1 or page > 1000 or sort not in ("score", "heat", "rank"):
        raise ValueError("分页或排序参数无效")
    if not isinstance(query, str) or len(query) > 100:
        raise ValueError("关键词不能超过100字")
    query = query.strip()
    key = f"{year}:{quarter}:{page}:{sort}:{query}"
    cached = (storage.read_json(CACHE_FILE, {}) or {}).get(key)
    if cached and not refresh and time.time() - cached["cached_at"] < CACHE_SECONDS:
        return {**copy.deepcopy(cached["result"]), "cached": True, "stale": False}
    try:
        result = metadata.bangumi_request(
            f"/v0/search/subjects?limit={PAGE_SIZE}&offset={(page - 1) * PAGE_SIZE}",
            {"keyword": query, "sort": sort, "filter": {"type": [2], "air_date": [">=" + start, "<" + end]}})
        if not isinstance(result.get("data"), list) or type(result.get("total")) is not int:
            raise ValueError("Bangumi 季度数据格式异常")
        subjects = []
        for item in result["data"]:
            subject = metadata.normalize_subject(item)
            subject["cover_url"] = (item.get("images") or {}).get("common") or subject["cover_url"]
            subjects.append(subject)
        payload = {"year": year, "quarter": quarter, "start_date": start, "end_date": end,
                   "page": page, "page_size": PAGE_SIZE, "total": result["total"], "sort": sort,
                   "query": query, "items": subjects, "source": "https://bangumi.github.io/api/",
                   "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                   "scope": "按首播/发行日期筛选公开动画条目，含TV、WEB、剧场版及OVA等；不含跨季续播或非公开条目"}
    except Exception as error:
        if cached:
            return {**copy.deepcopy(cached["result"]), "cached": True, "stale": True,
                    "warning": "Bangumi 暂不可用，显示上次缓存：" + safe_error(error)}
        raise
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with FileLock(CACHE_LOCK, timeout=15):
        cache = storage.read_json(CACHE_FILE, {}) or {}
        cache[key] = {"cached_at": time.time(), "result": payload}
        keep = sorted(cache, key=lambda k: cache[k]["cached_at"], reverse=True)[:48]
        storage.write_json(CACHE_FILE, {k: cache[k] for k in keep})
    return {**copy.deepcopy(payload), "cached": False, "stale": False}
