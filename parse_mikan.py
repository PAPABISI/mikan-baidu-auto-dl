# -*- coding: utf-8 -*-
"""蜜柑详情页解析器。
作为模块: `from parse_mikan import parse_mikan` 传入 HTML 文本，返回资源列表。
作为脚本: `python parse_mikan.py <html_file>` 打印统计。
"""
import re
import html as htmllib
import json
import sys


def parse_mikan(html_txt: str):
    """解析蜜柑番剧详情页 HTML，返回去重后的资源列表。"""
    chunks = html_txt.split('<tr>')
    items = []
    for c in chunks:
        if 'js-episode-select' not in c and 'magnet-link-wrap' not in c:
            continue
        m = re.search(r'data-magnet="([^"]+)"', c)
        if not m:
            continue
        magnet = htmllib.unescape(m.group(1))
        a = re.search(r'<a class="magnet-link-wrap"[^>]*>(.*?)</a>', c, re.S)
        title = htmllib.unescape(re.sub(r'<[^>]+>', '', a.group(1))).strip() if a else ''
        size = re.search(r'<td>\s*([\d.]+\s*[KMGT]i?B)\s*</td>', c)
        date = re.search(r'<td>\s*(\d{4}/\d{2}/\d{2} \d{2}:\d{2})\s*</td>', c)
        tor = re.search(r'href="(/Download/[^"]+\.torrent)"', c)
        items.append({
            'title': title,
            'magnet': magnet,
            'infohash': magnet.split('btih:')[1].split('&')[0] if 'btih:' in magnet else '',
            'size': size.group(1) if size else '',
            'date': date.group(1) if date else '',
            'torrent': 'https://mikanani.me' + tor.group(1) if tor else '',
        })

    # 去重
    seen, uniq = set(), []
    for it in items:
        if not it['infohash'] or it['infohash'] in seen:
            continue
        seen.add(it['infohash'])
        uniq.append(it)

    # 标注属性
    for it in uniq:
        t = it['title']
        it['group'] = subgroup(t)
        it['embedded'] = '内嵌' in t or '硬字幕' in t
        it['chs'] = any(k in t for k in ('简体', '简日', '简中', '简繁', 'CHS', 'GB'))
        it['cht'] = any(k in t for k in ('繁体', '繁日', '繁中', 'BIG5'))
        it['soft'] = any(k in t for k in ('外挂', '字幕文件', '简繁外挂'))
        it['ep'] = ep_of(t)
        it['lang'] = ('简体' if it['chs'] and not it['cht']
                      else '简繁' if it['chs'] and it['cht']
                      else '繁体' if it['cht'] else '未知')
    return uniq


def subgroup(t):
    m = re.search(r'[【\[]([^】\]]*?字幕组|[^】\]]*?压制组|[^】\]]*?字幕社|LoliHouse|Nekomoe|ANi|Sakurato|Kitauji)[】\]]', t)
    if m:
        return m.group(1)
    m = re.search(r'[【\[]([^】\]]*?)[】\]]', t)
    return m.group(1) if m else '未知'


def ep_of(t):
    m = re.search(r'[\[［]\s*(\d{1,3}(?:\.\d)?)\s*v?(\d)?\s*[\]］]', t)
    return m.group(1) if m else '?'


def _stats(items):
    strict = [i for i in items if i['embedded'] and i['lang'] == '简体' and not i['soft']]
    loose  = [i for i in items if i['embedded'] and i['chs'] and not i['soft'] and i not in strict]
    return strict, loose


if __name__ == "__main__":
    import collections
    path = sys.argv[1]
    h = open(path, encoding='utf-8').read()
    items = parse_mikan(h)
    strict, loose = _stats(items)
    print(f"总去重资源: {len(items)}")
    print(f"严格 简体内嵌: {len(strict)}")
    print(f"含简体但为简繁/双语内嵌: {len(loose)}")
    print()
    c = collections.Counter((i['group'], i['lang'],
                             '内嵌' if i['embedded'] else ('外挂' if i['soft'] else '其他'))
                            for i in items)
    print("== 全部资源标签分布 ==")
    for k, v in c.most_common():
        print(f"  {k[0]:<18} {k[1]:<6} {k[2]:<5} {v} 条")
    print()
    print("== 严格 简体内嵌 明细 ==")
    for i in sorted(strict, key=lambda x: x['ep']):
        print(f"  第{i['ep']:>3}话 | {i['group']:<12} | {i['size']:<9} | {i['date']} | {i['infohash']}")