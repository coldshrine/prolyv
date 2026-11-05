#!/usr/bin/env python3
"""Cards with no sources get the page where discovery saw the tea's name, when the master list has one. Usage: fill_sources.py"""
import glob, json, os
HERE = os.path.dirname(os.path.abspath(__file__))
rows = {x['slug']: x for x in json.load(open(os.path.join(HERE, 'logs', 'new-cards.json'), encoding='utf-8'))}
n = 0
for f in glob.glob(os.path.join(HERE, '..', 'teas', '*', '*.json')):
    if os.path.basename(f).startswith('_'):
        continue
    c = json.load(open(f, encoding='utf-8'))
    ev = [u for u in rows.get(c['slug'], {}).get('evidence') or [] if isinstance(u, str) and u.startswith('http')]
    if not c.get('sources') and ev:
        c['sources'] = ev[:2]
        json.dump(c, open(f, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        n += 1
print('filled', n)
