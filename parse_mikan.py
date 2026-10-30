# -*- coding: utf-8 -*-
"""蜜柑资源解析与配置筛选，Web 和自动任务共用。"""
import html
import json
import re
import sys
from decimal import Decimal
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit


def episode_info(title):
    """只返回普通单话；合集、SP/OVA/总集篇留给人工处理。"""
    if re.search(r"[\[【［]\s*(?:SP|OVA|OAD|OP|ED|NCOP|NCED)\s*\d*|特别篇|特別篇|总集篇|總集篇|合集|[\[［]\s*\d+\s*[-~～]\s*\d+", title, re.I):
        return "?", 0
    patterns = (
        r"[\[［]\s*(\d{1,3}(?:\.\d+)?)\s*(?:[vV](\d+))?\s*(?:END|完)?\s*[\]］]",
        r"第\s*(\d{1,3}(?:\.\d+)?)\s*(?:[vV](\d+))?\s*[话話集]",
        r"\bS\d{1,2}E(\d{1,3})(?:[vV](\d+))?\b",
        r"\s-\s*(\d{1,3}(?:\.\d+)?)(?:[vV](\d+))?(?=\s|\[|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, title, re.I)
        if match:
            tail = re.match(r"\s*[vV](\d+)", title[match.end():])
            return match.group(1), int(tail.group(1)) if tail else int(match.group(2) or 1)
    return "?", 0


def ep_of(title):
    return episode_info(title)[0]


def subgroup(title):
    names = re.findall(r"[【\[［]([^】\]］]+)[】\]］]", title)
    for name in names:
        if re.search(r"字幕[组組社]|[压壓]制[组組]|LoliHouse|Nekomoe|ANi|Sakurato|Kitauji", name, re.I):
            return name
    return names[0] if names else "未知"


class _ResourceParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.row = None
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr":
            self.row = {"title": [], "text": [], "magnet": "", "torrent": ""}
        if self.row is None:
            return
        if attrs.get("data-magnet", "").startswith("magnet:?"):
            self.row["magnet"] = attrs["data-magnet"]
        if tag == "a":
            self.in_title = "magnet-link-wrap" in attrs.get("class", "").split()
            href = attrs.get("href", "")
            if href.startswith("magnet:?"):
                self.row["magnet"] = href
            if href.startswith("/Download/"):
                self.row["torrent"] = "https://mikanani.me" + href

    def handle_data(self, data):
        if self.row is not None:
            self.row["text"].append(data)
            if self.in_title:
                self.row["title"].append(data)

    def handle_endtag(self, tag):
        if tag == "a":
            self.in_title = False
        if tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None
            self.in_title = False


def parse_mikan(html_txt):
    parser = _ResourceParser()
    parser.feed(html_txt)
    seen, items = set(), []
    for row in parser.rows:
        magnet = html.unescape(row["magnet"])
        hashes = parse_qs(urlsplit(magnet).query).get("xt", [])
        infohash = next((v[9:].lower() for v in hashes if v.lower().startswith("urn:btih:")), "")
        if not re.fullmatch(r"(?:[0-9a-f]{40}|[a-z2-7]{32})", infohash) or infohash in seen:
            continue
        title = "".join(row["title"]).strip()
        if not title:
            continue
        seen.add(infohash)
        text = " ".join(row["text"])
        size = re.search(r"\b[\d.]+\s*[KMGT]i?B\b", text, re.I)
        date = re.search(r"\d{4}[/\-]\d{2}[/\-]\d{2}\s+\d{2}:\d{2}", text)
        chs = bool(re.search(r"简体|简日|简中|简繁|(?<![A-Za-z])(?:CHS|GB(?:_CN)?)(?![A-Za-z])", title, re.I))
        cht = bool(re.search(r"繁体|繁日|繁中|简繁|(?<![A-Za-z])(?:CHT|BIG5)(?![A-Za-z])", title, re.I))
        ep, ver = episode_info(title)
        items.append({
            "title": title, "magnet": magnet, "infohash": infohash,
            "size": size.group() if size else "", "date": date.group() if date else "",
            "torrent": row["torrent"], "group": subgroup(title),
            "embedded": "内嵌" in title or "硬字幕" in title,
            "soft": any(word in title for word in ("外挂", "字幕文件", "軟字幕")),
            "chs": chs, "cht": cht, "ep": ep, "ver": ver,
            "lang": "简繁" if chs and cht else "简体" if chs else "繁体" if cht else "未知",
        })
    return items


def select_best(items, preferred_groups=()):
    """字幕组按配置顺序优先，同组选择最高修订版本。"""
    by_ep = {}
    for item in items:
        if item["ep"] == "?":
            continue
        ep = Decimal(item["ep"])
        group = item["group"]
        rank = preferred_groups.index(group) if group in preferred_groups else len(preferred_groups)
        score = (rank, -item.get("ver", 1), item["infohash"])
        if ep not in by_ep or score < by_ep[ep][0]:
            by_ep[ep] = (score, item)
    return [by_ep[ep][1] for ep in sorted(by_ep)]


def select_for_series(items, series):
    lang = series.get("language", "简体")
    filtered = [it for it in items
                if (lang == "不限" or it["lang"] == lang)
                and (not series.get("embedded_only", True) or (it["embedded"] and not it["soft"]))]
    return select_best(filtered, series.get("subtitle_groups", []))


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as stream:
        print(json.dumps(parse_mikan(stream.read()), ensure_ascii=False, indent=2))
