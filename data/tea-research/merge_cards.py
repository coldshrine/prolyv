#!/usr/bin/env python3
"""Apply duplicate decisions. Usage: python3 -I merge_cards.py patches/merges.jsonl
Each line: {"drop":"slug","into":"slug or null","reason":"..."}. With "into", the dropped card's names go into the kept card's aliases and its sources are added. The dropped file is deleted; git keeps it, logs/merged.jsonl records it."""
import glob, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
paths = {os.path.basename(f)[:-5]: f for f in glob.glob(os.path.join(HERE, '..', 'teas', '*', '*.json')) if not os.path.basename(f).startswith('_')}
done = errors = 0
for line in open(sys.argv[1], encoding='utf-8'):
    if not line.strip():
        continue
    row = json.loads(line)
    drop, into = row.get('drop'), row.get('into')
    if drop not in paths or (into and into not in paths) or drop == into:
        print('bad row', row); errors += 1; continue
    gone = json.load(open(paths[drop], encoding='utf-8'))
    if into:
        keep = json.load(open(paths[into], encoding='utf-8'))
        names = [gone.get('name_en'), gone.get('name_native'), gone.get('name_romanized'), gone.get('name_zh'), gone.get('name_ru'), *(gone.get('aliases') or [])]
        have = {str(a).lower() for a in (keep.get('aliases') or [])} | {str(keep.get(k) or '').lower() for k in ('name_en', 'name_native', 'name_romanized', 'name_zh', 'name_ru')}
        keep['aliases'] = (keep.get('aliases') or []) + [n for n in dict.fromkeys(names) if n and str(n).lower() not in have]
        keep['sources'] = list(dict.fromkeys((keep.get('sources') or []) + (gone.get('sources') or [])))[:6]
        json.dump(keep, open(paths[into], 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    os.remove(paths[drop])
    del paths[drop]
    with open(os.path.join(HERE, 'logs', 'merged.jsonl'), 'a', encoding='utf-8') as log:
        log.write(json.dumps({'drop': drop, 'into': into, 'reason': row.get('reason'), 'name_en': gone.get('name_en')}, ensure_ascii=False) + '\n')
    done += 1
print(f'{done} cards merged or dropped, {errors} problems')
sys.exit(1 if errors else 0)
