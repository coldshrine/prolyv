#!/usr/bin/env python3
"""Plan new cards from master lists. Usage: new_items.py
Dedupes across categories by zh and normalised English name, drops entries that already have a card, writes logs/new-cards.json and logs/items-new-t12.json + logs/items-new-t3.json (dossier inputs)."""
import glob, json, os, re, unicodedata
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..', 'teas')

def norm(s):
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode().lower()
    s = re.sub(r'\(.*?\)', ' ', s)
    s = re.sub(r'\b(tea|cha|the)\b', ' ', s)
    return re.sub(r'[^a-z0-9]+', '', s)

cards = {}
zh_seen, en_seen = {}, {}
for f in glob.glob(os.path.join(ROOT, '*', '*.json')):
    if os.path.basename(f).startswith('_'):
        continue
    c = json.load(open(f, encoding='utf-8'))
    cards[c['slug']] = c
    for z in (c.get('name_zh'), c.get('name_native')):
        if z and re.search(r'[一-鿿]', z) and c.get('category'):
            zh_seen[(z, c['category'])] = c['slug']
    en_seen[(norm(c['name_en']), c.get('category'))] = c['slug']

out, dropped, slugs = [], Counter(), set(cards)
order = {1: 0, 2: 1, 3: 2}
rows = []
for f in sorted(glob.glob(os.path.join(HERE, 'master', '*.json'))):
    rows += json.load(open(f, encoding='utf-8'))
rows.sort(key=lambda x: (order.get(x.get('tier'), 3), 0 if x.get('kind') != 'estate' else 1))
for x in rows:
    cat = x.get('category')
    if x.get('existing_slug') and x['existing_slug'] in cards:
        dropped['has card'] += 1; continue
    if x.get('kind') == 'estate' and x.get('tier') == 3:
        dropped['estate tier 3'] += 1; continue
    zh, en = x.get('zh'), norm(x.get('en'))
    if zh and (zh, cat) in zh_seen:
        dropped['zh dup'] += 1; continue
    if en and (en, cat) in en_seen:
        dropped['en dup'] += 1; continue
    slug = x.get('slug') or en
    slug = re.sub(r'[^a-z0-9]+', '-', slug.lower()).strip('-')[:70]
    if slug in slugs:
        slug = f'{slug}-{cat}'
    if slug in slugs:
        dropped['slug clash'] += 1; continue
    slugs.add(slug)
    x['slug'] = slug
    if zh:
        zh_seen[(zh, cat)] = slug
    if en:
        en_seen[(en, cat)] = slug
    out.append(x)
json.dump(out, open(os.path.join(HERE, 'logs', 'new-cards.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
def item(x, thin=False):
    return dict(slug=x['slug'], en=x.get('en'), zh=x.get('zh'), ru=x.get('ru'), native=x.get('native'), aliases=x.get('aliases') or [], country=x.get('country'), evidence=x.get('evidence') or [], wikipedia_title=None, wikipedia_scope=None, thin=thin)
json.dump([item(x) for x in out if x.get('tier') in (1, 2)], open(os.path.join(HERE, 'logs', 'items-new-t12.json'), 'w', encoding='utf-8'), ensure_ascii=False)
json.dump([item(x, True) for x in out if x.get('tier') == 3], open(os.path.join(HERE, 'logs', 'items-new-t3.json'), 'w', encoding='utf-8'), ensure_ascii=False)
print('new cards', len(out), dict(Counter(x.get('tier') for x in out)), 'by cat', dict(Counter(x.get('category') for x in out)))
print('dropped', dict(dropped))
