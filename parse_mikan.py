import re, html, json, sys

path = sys.argv[1]
h = open(path, encoding='utf-8').read()

chunks = h.split('<tr>')
items = []
for c in chunks:
    if 'js-episode-select' not in c:
        continue
    m = re.search(r'data-magnet="([^"]+)"', c)
    if not m:
        continue
    magnet = html.unescape(m.group(1))
    a = re.search(r'<a class="magnet-link-wrap"[^>]*href="(/Home/Episode/[^"]+)"[^>]*>(.*?)</a>', c, re.S)
    title = html.unescape(re.sub(r'<[^>]+>', '', a.group(2))).strip() if a else ''
    ep = a.group(1) if a else ''
    size = re.search(r'<td>\s*([\d.]+\s*[KMGT]i?B)\s*</td>', c)
    date = re.search(r'<td>\s*(\d{4}/\d{2}/\d{2} \d{2}:\d{2})\s*</td>', c)
    tor = re.search(r'href="(/Download/[^"]+\.torrent)"', c)
    items.append({
        'title': title,
        'magnet': magnet,
        'infohash': magnet.split('btih:')[1].split('&')[0] if 'btih:' in magnet else '',
        'page': 'https://mikanani.me' + ep,
        'size': size.group(1) if size else '',
        'date': date.group(1) if date else '',
        'torrent': 'https://mikanani.me' + tor.group(1) if tor else '',
    })

# 去重（同一资源可能出现多次）
seen, uniq = set(), []
for it in items:
    if it['infohash'] in seen:
        continue
    seen.add(it['infohash'])
    uniq.append(it)

def subgroup(t):
    m = re.search(r'[【\[]([^】\]]*?字幕组|[^】\]]*?压制组|[^】\]]*?字幕社|LoliHouse|Nekomoe|ANi|Sakurato|Kitauji)[】\]]', t)
    if m:
        return m.group(1)
    m = re.search(r'[【\[](.*?)[】\]]', t)
    return m.group(1) if m else '未知'

def has(t, *keys):
    return any(k in t for k in keys)

for it in uniq:
    t = it['title']
    it['group'] = subgroup(t)
    it['embedded'] = '内嵌' in t or '硬字幕' in t
    it['chs'] = has(t, '简体', '简日', '简中', '简繁', 'CHS', 'GB')
    it['cht'] = has(t, '繁体', '繁日', '繁中', 'BIG5')
    it['soft'] = has(t, '外挂', '字幕文件', '简繁外挂')
    it['ep'] = (re.search(r'[\[［](\d{1,3}(?:\.\d)?|SP\d*)[\]］]', t) or [None, '?'])[1] if True else '?'
    it['lang'] = '简体' if it['chs'] and not it['cht'] else ('简繁' if it['chs'] and it['cht'] else ('繁体' if it['cht'] else '未知'))

strict = [i for i in uniq if i['embedded'] and i['lang'] == '简体' and not i['soft']]
loose  = [i for i in uniq if i['embedded'] and i['chs'] and not i['soft'] and i not in strict]

print(f"总去重资源: {len(uniq)}")
print(f"严格 简体内嵌: {len(strict)}")
print(f"含简体但为简繁/双语内嵌: {len(loose)}")
print()
from collections import Counter
print("== 全部资源标签分布 ==")
c = Counter((i['group'], i['lang'], '内嵌' if i['embedded'] else ('外挂' if i['soft'] else '其他')) for i in uniq)
for k, v in c.most_common():
    print(f"  {k[0]:<18} {k[1]:<6} {k[2]:<5} {v} 条")
print()
print("== 严格 简体内嵌 明细 ==")
for i in sorted(strict, key=lambda x: x['ep']):
    print(f"  第{i['ep']:>3}话 | {i['group']:<12} | {i['size']:<9} | {i['date']} | {i['infohash']}")

json.dump({'name': '', 'strict': strict, 'loose': loose, 'all': uniq}, open('/tmp/b4059.json','w',encoding='utf-8'), ensure_ascii=False, indent=1)
print("\n已写入 /tmp/b4059.json")
