#!/usr/bin/env python3
"""Existing cards as dossier items. Usage: cards_as_items.py > items.json"""
import glob, json, os, re
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'teas')
out = []
for f in sorted(glob.glob(os.path.join(ROOT, '*', '*.json'))):
    if os.path.basename(f).startswith('_'):
        continue
    c = json.load(open(f, encoding='utf-8'))
    native = c.get('name_native')
    zh = c.get('name_zh') or (native if native and re.search(r'[一-鿿]', native) and c.get('origin_country') in ('China', 'Taiwan', 'Hong Kong') else None)
    aliases = [a for a in c.get('aliases') or []]
    out.append(dict(slug=c['slug'], en=c['name_en'], zh=zh, ru=c.get('name_ru'), native=native, aliases=aliases, country=c.get('origin_country'),
                    wikipedia_title=c.get('wikipedia_title'), wikipedia_scope=c.get('wikipedia_scope') or 'none', evidence=c.get('sources') or []))
print(json.dumps(out, ensure_ascii=False))
