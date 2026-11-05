#!/usr/bin/env python3
"""Batches for the translation pass. Usage: make_i18n_batches.py <min popularity> [size]; prints batch ids. Cards already translated or in a batch are left out."""
import glob, json, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..', 'teas')
lo = int(sys.argv[1]); size = int(sys.argv[2]) if len(sys.argv) > 2 else 6
planned = set()
for f in glob.glob(os.path.join(HERE, 'batches', 't-*.slugs')):
    planned |= set(open(f).read().split())
todo = []
for f in glob.glob(os.path.join(ROOT, '*', '*.json')):
    if os.path.basename(f).startswith('_'):
        continue
    c = json.load(open(f, encoding='utf-8'))
    if (c.get('popularity') or 1) >= lo and not c.get('stub') and not (c.get('i18n') or {}).get('ru') and c['slug'] not in planned:
        todo.append(c)
todo.sort(key=lambda c: (-(c.get('popularity') or 1), c['slug']))
KEYS = 'name_en name_zh name_ru category origin_country origin_region processing harvest_season appearance_dry appearance_liquor appearance_wet taste_profile vintage_note history description'.split()
existing = len(glob.glob(os.path.join(HERE, 'batches', 't-*.md')))
ids = []
for i in range(0, len(todo), size):
    bid = f't-{existing + i // size:03d}'
    chunk = todo[i:i + size]
    blocks = []
    for c in chunk:
        row = {k: c.get(k) for k in KEYS}
        row['brewing_notes'] = (c.get('brewing') or {}).get('notes')
        blocks.append(f"## {c['slug']}\n" + json.dumps(row, ensure_ascii=False, indent=0))
    open(os.path.join(HERE, 'batches', bid + '.md'), 'w', encoding='utf-8').write('\n\n'.join(blocks))
    open(os.path.join(HERE, 'batches', bid + '.slugs'), 'w').write(' '.join(c['slug'] for c in chunk))
    ids.append(bid)
print(json.dumps({'batches': ids, 'cards': len(todo)}))
