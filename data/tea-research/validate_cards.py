#!/usr/bin/env python3
"""Validate tea cards. Usage: python3 -I validate_cards.py [--extended] [file ...]; no files = every card.
Prints only problems plus a one-line total. Exit 1 if anything fails."""
import glob, json, os, re, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'teas')
TAGS = set('floral fruity stone-fruit citrus dried-fruit honey sweet creamy nutty cocoa malty roasted smoky mineral grassy vegetal umami marine woody earthy camphor medicinal spicy herbal bitter brisk tart'.split())
CATS = set('green white yellow oolong black sheng shu heicha tisane'.split())
FORMS = set('loose cake brick tuo ball powder stick other'.split())
BASE = 'slug name_en category origin_country origin_region processing form harvest_season appearance_dry appearance_liquor appearance_wet taste_profile taste_tags brewing caffeine_level vintage_note description wikipedia_scope popularity'.split()
EXT = 'name_zh name_ru history liquor_hex leaf_hex sources confidence'.split()
PROCESS = re.compile(r"dossier|\b(this|the) card\b|\bour (search|sources|notes|list)\b|\bwe (could|did|found|have|read|were|cannot|can't|couldn't|didn't)\b|\bwikipedia\b|\bwikidata\b|search result|this run|\bpages? we\b|earlier notes|fact-check", re.I)
args = sys.argv[1:]
extended = '--extended' in args
files = [a for a in args if not a.startswith('--')] or sorted(f for f in glob.glob(os.path.join(ROOT, '*', '*.json')) if not os.path.basename(f).startswith('_'))
bad = 0
for f in files:
    probs = []
    try:
        c = json.load(open(f, encoding='utf-8'))
    except Exception as e:
        print(f'{f}: invalid JSON: {e}'); bad += 1; continue
    for k in BASE + (EXT if extended else []):
        if c.get(k) in (None, '', [], {}) and k != 'wikipedia_scope':
            probs.append('missing ' + k)
    if c.get('slug') and c['slug'] != os.path.basename(f)[:-5]: probs.append('slug != filename')
    if c.get('slug') and not re.fullmatch(r'[a-z0-9]+(-[a-z0-9]+)*', c['slug']): probs.append('bad slug')
    if c.get('category') not in CATS: probs.append(f'bad category {c.get("category")}')
    if c.get('form') not in FORMS: probs.append(f'bad form {c.get("form")}')
    if c.get('caffeine_level') not in ('none', 'low', 'medium', 'high'): probs.append('bad caffeine_level')
    unk = [t for t in c.get('taste_tags') or [] if t not in TAGS]
    if unk: probs.append('unknown tags ' + ','.join(unk))
    b = c.get('brewing') or {}
    if b and len(b.get('seconds') or []) != b.get('steeps'): probs.append('brewing.seconds length != steeps')
    if len((c.get('description') or '').split('\n\n')) < 2: probs.append('description needs 2+ paragraphs')
    for k in ('liquor_hex', 'leaf_hex'):
        if c.get(k) and not re.fullmatch(r'#[0-9A-Fa-f]{6}', c[k]): probs.append('bad ' + k)
    if c.get('name_zh') and not re.search(r'[一-鿿]', c['name_zh']): probs.append('name_zh has no Chinese')
    if c.get('name_ru') and not re.search(r'[А-Яа-яЁё]', c['name_ru']): probs.append('name_ru has no Cyrillic')
    if c.get('confidence') and c['confidence'] not in ('high', 'medium', 'low'): probs.append('bad confidence')
    if extended and not c.get('sources') and c.get('confidence') == 'low':
        probs = [p for p in probs if p != 'missing sources']
    if c.get('stub'):
        probs = [p for p in probs if p != 'missing history']
    region = c.get('origin_region') or ''
    if len(region) > 60 or ';' in region:
        probs.append('origin_region must be a short place name, under 60 characters, no sentences')
    texts = [c.get(k) or '' for k in ('history', 'description', 'processing', 'harvest_season', 'appearance_dry', 'appearance_liquor', 'appearance_wet', 'taste_profile', 'vintage_note')]
    process = PROCESS.search(' '.join(texts))
    if process:
        probs.append(f'reader text mentions our research process: "{process.group(0)}"; say what is or is not documented instead')
    for u in c.get('sources') or []:
        if not str(u).startswith('http'): probs.append('bad source ' + str(u)[:40])
    i18n = c.get('i18n') or {}
    for lang, rx in (('ru', r'[А-Яа-яЁё]'), ('zh', r'[一-鿿]')):
        block = i18n.get(lang)
        if block:
            for k in ('processing', 'harvest_season', 'appearance_dry', 'appearance_liquor', 'appearance_wet', 'taste_profile', 'vintage_note', 'description'):
                if not re.search(rx, block.get(k) or ''):
                    probs.append(f'i18n.{lang}.{k} missing or in the wrong language')
            if c.get('history') and not re.search(rx, block.get('history') or ''):
                probs.append(f'i18n.{lang}.history missing')
    if probs:
        bad += 1
        print(os.path.relpath(f, ROOT) + ': ' + '; '.join(probs))
print(f'checked {len(files)} cards, {bad} with problems')
sys.exit(1 if bad else 0)
