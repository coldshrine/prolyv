#!/usr/bin/env python3
"""Apply a photo checker's picks. Usage: python3 -I apply_photos.py photos/picks/<batch>.jsonl
Each line: {"slug":"...","pick":[{"n":2,"kind":"dry_leaf","alt":"..."}],"why_none":"..."}; an empty pick stores images: []."""
import glob, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..', 'teas')
KINDS = {'dry_leaf', 'liquor', 'wet_leaf', 'cake', 'plant', 'plantation', 'serving', 'other'}
cards = {os.path.basename(f)[:-5]: f for f in glob.glob(os.path.join(ROOT, '*', '*.json')) if not os.path.basename(f).startswith('_')}
errors = stored = images = 0
for n, line in enumerate(open(sys.argv[1], encoding='utf-8'), 1):
    if not line.strip():
        continue
    try:
        row = json.loads(line)
    except ValueError as e:
        print(f'line {n}: invalid JSON: {e}'); errors += 1; continue
    slug = row.get('slug')
    if slug not in cards:
        print(f'line {n}: unknown slug {slug}'); errors += 1; continue
    cands = {c['n']: c for c in json.load(open(os.path.join(HERE, 'photos', slug + '.json'), encoding='utf-8'))['candidates']}
    out = []
    for p in row.get('pick') or []:
        c = cands.get(p.get('n'))
        if not c or p.get('kind') not in KINDS or not p.get('alt'):
            print(f'{slug}: bad pick {p}'); errors += 1; continue
        out.append({k: c.get(k) for k in ('url', 'thumb_url', 'source_page', 'author', 'license', 'license_url')} | {'kind': p['kind'], 'alt': p['alt']})
    card = json.load(open(cards[slug], encoding='utf-8'))
    card['images'] = out
    json.dump(card, open(cards[slug], 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    stored += 1; images += len(out)
print(f'{stored} teas updated, {images} photos stored, {errors} problems')
sys.exit(1 if errors else 0)
