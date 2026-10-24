import re, html, sys, json, urllib.parse
from collections import OrderedDict

src = sys.argv[1]
out = sys.argv[2]

h = open(src, encoding='utf-8').read()
name = html.unescape(re.search(r'<title>Mikan Project - ([^<]+)</title>', h).group(1)).strip()

chunks = [c for c in h.split('<tr>') if 'js-episode-select' in c]
seen, rows = set(), []
for c in chunks:
    m = re.search(r'data-magnet="([^"]+)"', c)
    if not m:
        continue
    mag = html.unescape(m.group(1))
    ih = mag.split('btih:')[1].split('&')[0]
    if ih in seen:
        continue
    seen.add(ih)
    a = re.search(r'<a class="magnet-link-wrap"[^>]*>(.*?)</a>', c, re.S)
    t = html.unescape(re.sub(r'<[^>]+>', '', a.group(1))).strip()
    size = re.search(r'<td>\s*([\d.]+\s*[KMGT]i?B)\s*</td>', c)
    date = re.search(r'<td>\s*(\d{4}/\d{2}/\d{2} \d{2}:\d{2})\s*</td>', c)
    if not ('内嵌' in t and ('简体' in t or '简日' in t or '简中' in t) and '外挂' not in t):
        continue
    grp = re.search(r'[【\[]([^】\]]*?字幕组|[^】\]]*?字幕社|[^】\]]*?制作组|SFSub|Nix-Raws|ANi)', t)
    grp = grp.group(1) if grp else '未知'
    epm = re.search(r'[\[［]\s*(\d{1,3})\s*(v\d)?\s*[\]］]', t) or re.search(r'-\s*(\d{1,3})\s*[\[［]', t)
    ep = int(epm.group(1)) if epm else 0
    ver = epm.group(2) if (epm and epm.lastindex and epm.lastindex >= 2 and epm.group(2)) else ''
    rows.append({'ep': ep, 'ver': ver, 'group': grp, 'title': t,
                 'size': (size.group(1).strip() if size else '').replace(' ', ''),
                 'date': date.group(1) if date else '', 'magnet': mag, 'hash': ih})

rows.sort(key=lambda r: (r['ep'], r['group'], r['ver']))

rec_group = '绿茶字幕组'
rec = [r for r in rows if r['group'] == rec_group]
best = {}
for r in rec:
    if r['ep'] not in best or r['ver']:
        best[r['ep']] = r
rec = [best[k] for k in sorted(best)]

lines = []
lines.append(f"# {name}  磁力清单（简体内嵌）")
lines.append(f"# 来源: https://mikanani.me/Home/Bangumi/4059")
lines.append(f"# 筛选条件: 简体内嵌（含[简日]/[简日双语]内嵌），已排除繁体外挂/简体外挂")
lines.append(f"# 导出: 2026-10-18  by WorkBuddy")
lines.append(f"# 命中 {len(rows)} 条")
lines.append("")
lines.append("[推荐成套] 绿茶字幕组 1080p WebRip 简日内嵌 —— 覆盖 01-03 完整，共 3 个文件")
for r in rec:
    lines.append(f"  第{r['ep']:02d}话 {r['ver']}  {r['size']}  {r['magnet']}")
lines.append("")
lines.append("=" * 70)
lines.append("[全部简体内嵌资源] 按话数排列")
cur = None
for r in rows:
    if r['ep'] != cur:
        cur = r['ep']
        lines.append("")
        lines.append(f"--- 第 {cur:02d} 话 ---" if cur else "--- 话数未识别 ---")
    lines.append(f"{r['group']} | {r['size']} | {r['date']} | {r['ver']}")
    lines.append(f"  {r['title']}")
    lines.append(f"  {r['magnet']}")

txt = '\n'.join(lines)
open(out, 'w', encoding='utf-8').write(txt)

folder = name.split(' / ')[0].strip()
print("番名:", name)
print("文件夹名:", folder)
print("目录路径(需urlencode):", urllib.parse.quote('/' + folder))
print("命中条数:", len(rows), "| 推荐套:", len(rec))
print("清单字符数:", len(txt))
print("推荐套总大小:", ', '.join(r['size'] for r in rec))
