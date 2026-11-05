#!/usr/bin/env python3
"""Compact index of existing cards, one per line: slug | name_en | name_zh-or-native | category | country | group."""
import glob, json, os
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'teas')
for f in sorted(glob.glob(os.path.join(ROOT, '*', '*.json'))):
    if os.path.basename(f).startswith('_'): continue
    try: c = json.load(open(f, encoding='utf-8'))
    except Exception: continue
    print(' | '.join(str(x) for x in [c.get('slug'), c.get('name_en'), c.get('name_zh') or c.get('name_native') or '', c.get('category'), c.get('origin_country'), os.path.basename(os.path.dirname(f))]))
