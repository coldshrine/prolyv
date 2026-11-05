#!/usr/bin/env python3
"""Group teas that have photo candidates into checking batches; teas with none get images: [] right away. Usage: make_photo_batches.py [size]; prints batch ids."""
import glob, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..', 'teas')
size = int(sys.argv[1]) if len(sys.argv) > 1 else 15
cards = {}
for f in glob.glob(os.path.join(ROOT, '*', '*.json')):
    if not os.path.basename(f).startswith('_'):
        cards[os.path.basename(f)[:-5]] = f
planned = set()
REDO = os.environ.get('REDO')  # second pass over re-ranked candidates: only files written since then, tracked apart from the first pass
SINCE = float(open(os.path.join(HERE, 'logs', 'redo-since')).read()) if REDO else 0
PREFIX = 'q' if REDO else 'p'
for f in glob.glob(os.path.join(HERE, 'batches', PREFIX + '-*.slugs')):
    planned |= set(open(f, encoding='utf-8').read().split())
todo, empty = [], 0
for f in sorted(glob.glob(os.path.join(HERE, 'photos', '*.json'))):
    if os.path.getmtime(f) < SINCE:
        continue
    d = json.load(open(f, encoding='utf-8'))
    slug = d['slug']
    if slug not in cards or slug in planned:
        continue
    c = json.load(open(cards[slug], encoding='utf-8'))
    if 'images' in c and c['images']:
        continue
    if not d['candidates']:
        if 'images' not in c:
            c['images'] = []
            json.dump(c, open(cards[slug], 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
            empty += 1
        continue
    todo.append((c, d['candidates']))
existing = len(glob.glob(os.path.join(HERE, 'batches', PREFIX + '-*.md')))
ids = []
for i in range(0, len(todo), size):
    bid = f'{PREFIX}-{existing + i // size:03d}'
    blocks = []
    for c, cands in todo[i:i + size]:
        lines = [f"## {c['slug']} | {c['name_en']} | {c.get('name_zh') or c.get('name_native') or ''} | {c['category']} | {c.get('origin_country')} · {c.get('origin_region')}",
                 f"form: {c.get('form')}; dry leaf: {c.get('appearance_dry')}; liquor: {c.get('appearance_liquor')}",
                 f"SHEET: {os.path.join(HERE, 'photos', 'sheets', c['slug'] + '.jpg')}"]
        for x in cands:
            lines.append(f"{x['n']}. {x['title']} | {x['desc'][:160]} | {x['license']} | {x['size']}")
        blocks.append('\n'.join(lines))
    open(os.path.join(HERE, 'batches', bid + '.md'), 'w', encoding='utf-8').write('\n\n'.join(blocks))
    open(os.path.join(HERE, 'batches', bid + '.slugs'), 'w', encoding='utf-8').write(' '.join(c['slug'] for c, _ in todo[i:i + size]))
    ids.append(bid)
print(json.dumps({'batches': ids, 'teas_to_check': len(todo), 'set_empty': empty}))
