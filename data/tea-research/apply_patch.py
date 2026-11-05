#!/usr/bin/env python3
"""Apply a writer's patch to the tea cards. Usage: python3 -I apply_patch.py patches/<id>.jsonl
Each line: {"slug", extended fields..., "fixes": {base field: corrected value}, "fix_notes": "why"}. Prints validator problems; exit 1 if any."""
import glob, json, os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..', 'teas')
EXT = {'name_zh', 'name_ru', 'history', 'liquor_hex', 'leaf_hex', 'sources', 'confidence', 'i18n', 'images'}
FIXABLE = {'name_en', 'name_native', 'name_romanized', 'aliases', 'origin_country', 'origin_region', 'cultivar', 'processing', 'form', 'harvest_season', 'appearance_dry', 'appearance_liquor', 'appearance_wet', 'taste_profile', 'taste_tags', 'brewing', 'caffeine_level', 'vintage_note', 'description', 'wikipedia_title', 'wikipedia_scope', 'popularity', 'category'}
paths = {os.path.basename(f)[:-5]: f for f in glob.glob(os.path.join(ROOT, '*', '*.json')) if not os.path.basename(f).startswith('_')}
touched, errors = [], 0
for n, line in enumerate(open(sys.argv[1], encoding='utf-8'), 1):
    if not line.strip():
        continue
    try:
        row = json.loads(line)
    except ValueError as e:
        print(f'line {n}: invalid JSON: {e}'); errors += 1; continue
    f = paths.get(row.get('slug'))
    if not f:
        print(f'line {n}: unknown slug {row.get("slug")}'); errors += 1; continue
    card = json.load(open(f, encoding='utf-8'))
    for k, v in row.items():
        if k in EXT and v not in (None, ''):
            if k == 'i18n' and isinstance(card.get('i18n'), dict):
                merged = dict(card['i18n']); merged.update(v); v = merged
            card[k] = v
    fixes = row.get('fixes') or {}
    bad = [k for k in fixes if k not in FIXABLE]
    if bad:
        print(f'{row["slug"]}: field not fixable: {bad}'); errors += 1
    for k, v in fixes.items():
        if k in FIXABLE:
            card[k] = v
    if fixes or row.get('doubt'):
        with open(os.path.join(HERE, 'logs', 'fixes.jsonl'), 'a', encoding='utf-8') as log:
            log.write(json.dumps({'slug': row['slug'], 'patch': os.path.basename(sys.argv[1]), 'fields': sorted(fixes), 'notes': row.get('fix_notes'), 'doubt': row.get('doubt')}, ensure_ascii=False) + '\n')
    with open(f, 'w', encoding='utf-8') as out:
        json.dump(card, out, ensure_ascii=False, indent=2); out.write('\n')
    touched.append(f)
r = subprocess.run([sys.executable, '-I', os.path.join(HERE, 'validate_cards.py'), '--extended', *touched], capture_output=True, text=True)
print(r.stdout.strip())
sys.exit(1 if errors or r.returncode else 0)
