#!/usr/bin/env python3
"""How many dossiers are ready and not yet in a batch. Usage: ready_count.py <tiers, e.g. 1,2>"""
import glob, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
tiers = {int(t) for t in sys.argv[1].split(',')}
rows = json.load(open(os.path.join(HERE, 'logs', 'new-cards.json'), encoding='utf-8'))
planned = set()
for f in glob.glob(os.path.join(HERE, 'batches', '*.slugs')):
    planned |= set(open(f, encoding='utf-8').read().split())
print(sum(1 for x in rows if x.get('tier') in tiers and x['slug'] not in planned and os.path.exists(os.path.join(HERE, 'dossier', x['slug'] + '.json'))))
