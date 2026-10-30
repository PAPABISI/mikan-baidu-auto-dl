"""公开番剧资料。Bangumi 搜索结果只提供候选，绑定条目需由人或 Agent 确认。"""
import collections
import datetime as dt
import json
import os
import re
import urllib.parse
import urllib.request

from baidu import make_opener

AGENT = "PAPABISI/mikan-baidu-auto-dl (local anime library; https://github.com/PAPABISI/mikan-baidu-auto-dl)"


def bangumi_request(path, body=None):
    proxy = os.environ.get("BANGUMI_PROXY", os.environ.get("MIKAN_PROXY", "http://127.0.0.1:7897"))
    opener, _ = make_opener(proxy=proxy)
    request = urllib.request.Request("https://api.bgm.tv" + path,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"User-Agent": AGENT, "Content-Type": "application/json"})
    with opener.open(request, timeout=25) as response:
        return json.load(response)


def normalize_subject(data):
    info = {field["key"]: field.get("value") for field in data.get("infobox", [])}
    day = str(info.get("放送星期", ""))
    weekday = next((i for i, label in enumerate("一二三四五六日", 1) if label in day), None)
    if "天" in day:
        weekday = 7
    episodes = str(info.get("话数") or "")
    main_episodes = int(episodes) if re.fullmatch(r"\d+", episodes) else data.get("eps")
    return {
        "bangumi_id": data["id"], "name_original": data.get("name", ""),
        "name_cn": data.get("name_cn", ""), "summary": (data.get("summary") or "").split("[简介原文]")[0].strip(),
        "cover_url": (data.get("images") or {}).get("large") or data.get("image"),
        "total_episodes": main_episodes or data.get("total_episodes"),
        "air_date": data.get("date"), "broadcast_weekday": weekday,
        "broadcast_source": f"https://bgm.tv/subject/{data['id']}",
        "broadcast_note": "每周更新以 Bangumi 条目的放送星期为准，不按字幕发布时间或时区换算星期",
        "broadcast_time": None, "broadcast_timezone": None,
        "score": (data.get("rating") or {}).get("score"),
        "score_votes": (data.get("rating") or {}).get("total", 0),
        "rank": (data.get("rating") or {}).get("rank"), "platform": data.get("platform", ""),
        "studio": info.get("动画制作", ""), "official_url": info.get("官方网站", ""),
        "metadata_updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def search_bangumi(query):
    result = bangumi_request("/v0/search/subjects?limit=8", {"keyword": query, "filter": {"type": [2]}})
    return [normalize_subject(item) for item in result.get("data", [])]


def get_subject(subject_id):
    if type(subject_id) is not int or subject_id <= 0:
        raise ValueError("Bangumi ID 必须是正整数")
    return normalize_subject(bangumi_request(f"/v0/subjects/{subject_id}"))


def release_pattern(items):
    counts = collections.Counter()
    for item in items:
        try:
            counts[dt.datetime.strptime(item["date"], "%Y/%m/%d %H:%M").isoweekday()] += 1
        except (ValueError, KeyError):
            pass
    days = sorted(day for day, count in counts.items() if count >= 2)
    return {"release_weekdays": days, "release_note": "根据蜜柑所选资源发布时间推测，非字幕组承诺",
            "release_samples": sum(counts.values())}
