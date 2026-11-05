#!/usr/bin/env python3
"""Merge found/*.jsonl by exact keys into merged/<category>.json and print a small JSON summary.
Rows sharing a Chinese name or a normalised English name collapse into one; meaning-level merging is left for the consolidators."""
import glob, json, os, re, unicodedata
HERE = os.path.dirname(os.path.abspath(__file__))
os.makedirs(os.path.join(HERE, 'merged'), exist_ok=True)
CATS = 'green white yellow oolong black sheng shu heicha tisane'.split()
def norm(s):
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode().lower()
    s = re.sub(r"\b(tea|cha|the|chai)\b", '', s)
    return re.sub(r'[^a-z0-9]+', '', s)
rows, bad = [], 0
for f in sorted(glob.glob(os.path.join(HERE, 'found', '*.jsonl'))):
    for line in open(f, encoding='utf-8'):
        line = line.strip()
        if not line: continue
        try: r = json.loads(line)
        except Exception: bad += 1; continue
        if not isinstance(r, dict) or not (r.get('en') or r.get('zh') or r.get('native')): bad += 1; continue
        r['_src'] = os.path.basename(f)[:-6]; rows.append(r)
parent = list(range(len(rows)))
def find(x):
    while parent[x] != x:
        parent[x] = parent[parent[x]]; x = parent[x]
    return x
seen = {}
for i, r in enumerate(rows):
    keys = []
    if r.get('zh'): keys.append('z:' + re.sub(r'\s+', '', r['zh']))
    if norm(r.get('en')): keys.append('e:' + norm(r['en']))
    for k in keys:
        if k in seen: parent[find(i)] = find(seen[k])
        else: seen[k] = i
groups = {}
for i, r in enumerate(rows): groups.setdefault(find(i), []).append(r)
merged = {c: [] for c in CATS}; merged['unknown'] = []
for g in groups.values():
    def vals(k): return sorted({str(r[k]).strip() for r in g if r.get(k)})
    cats = [r.get('category') for r in g if r.get('category') in CATS]
    cat = max(set(cats), key=cats.count) if cats else 'unknown'
    merged[cat].append({'en': vals('en'), 'zh': vals('zh'), 'ru': vals('ru'), 'native': vals('native'), 'country': vals('country'), 'region': vals('region')[:4], 'kind': vals('kind'), 'evidence': vals('evidence')[:5], 'sources': sorted({r['_src'] for r in g})})
for c, items in merged.items():
    items.sort(key=lambda x: (x['en'] or x['zh'] or x['native'] or [''])[0].lower())
    json.dump(items, open(os.path.join(HERE, 'merged', c + '.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
state = os.path.join(HERE, 'merged', '.count'); prev = int(open(state).read()) if os.path.exists(state) else 0
total = sum(len(v) for v in merged.values()); open(state, 'w').write(str(total))
print(json.dumps({'raw_rows': len(rows), 'bad_lines': bad, 'merged_total': total, 'new_since_last': total - prev, 'by_category': {c: len(v) for c, v in merged.items()}}))
