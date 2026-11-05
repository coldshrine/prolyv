#!/usr/bin/env python3
"""Group existing cards into writer batches: batches/<id>.md holds the card, any fact-check note and the dossier.
Usage: make_batches.py enrich [size] ; prints the JSON list of batch ids. Only cards whose dossier exists and that still lack `history` are taken."""
import glob, json, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..', 'teas')
BATCH = os.path.join(HERE, 'batches')
os.makedirs(BATCH, exist_ok=True)

def dossier_text(slug):
    p = os.path.join(HERE, 'dossier', slug + '.json')
    if not os.path.exists(p):
        return None
    d = json.load(open(p, encoding='utf-8'))
    out = []
    wd = d.get('wikidata')
    if wd:
        out.append(f"WIKIDATA {wd['url']} labels={json.dumps(wd['labels'], ensure_ascii=False)} aliases={json.dumps(wd.get('aliases'), ensure_ascii=False)} desc={wd.get('description_en')}")
    for p in d.get('pages', []):
        out.append(f"PAGE [{p['lang']}] {p['title']} {p['url']}\n{p['text']}")
    for p in d.get('mentions', []):
        out.append(f"MENTIONS in [{p['lang']}] {p['title']} {p['url']}\n{p['text']}")
    for p in d.get('evidence', []):
        out.append(f"EVIDENCE {p['url']} ({p['title']})\n{p['text']}")
    return '\n\n'.join(out) if out else '(nothing found mechanically: thin dossier, search the web for this one)'

def reviews(group):
    notes = {}
    p = os.path.join(ROOT, group, '_review.md')
    if os.path.exists(p):
        for line in open(p, encoding='utf-8'):
            m = re.match(r'([a-z0-9-]+) — (.+)', line.strip())
            if m and not m.group(2).startswith('OK'):
                notes[m.group(1)] = m.group(2)
    return notes

def card_block(path, notes):
    c = json.load(open(path, encoding='utf-8'))
    slim = {k: v for k, v in c.items() if k not in ('brewing', 'wikipedia_scope', 'popularity')}
    out = f"## CARD {c['slug']}\n" + json.dumps(slim, ensure_ascii=False, indent=0)
    if notes.get(c['slug']):
        out += f"\nEARLIER FACT-CHECK NOTE: {notes[c['slug']]}"
    return out + "\n\n### DOSSIER\n" + (dossier_text(c['slug']) or '(no dossier)') + "\n"

def new_batches(tiers, size, prefix):
    rows = json.load(open(os.path.join(HERE, 'logs', 'new-cards.json'), encoding='utf-8'))
    have = {os.path.basename(f)[:-5] for f in glob.glob(os.path.join(ROOT, '*', '*.json'))}
    planned = set()
    for f in glob.glob(os.path.join(BATCH, 'n-*.slugs')):
        planned |= set(open(f, encoding='utf-8').read().split())
    todo = [x for x in rows if x.get('tier') in tiers and x['slug'] not in have and x['slug'] not in planned and os.path.exists(os.path.join(HERE, 'dossier', x['slug'] + '.json'))]
    todo.sort(key=lambda x: (x.get('category') or '', x.get('country') or '', x.get('region') or '', x['slug']))
    ids = []
    existing = len(glob.glob(os.path.join(BATCH, f'{prefix}-*.md')))
    for i in range(0, len(todo), size):
        bid = f'{prefix}-{existing + i // size:03d}'
        chunk = todo[i:i + size]
        blocks = []
        for x in chunk:
            row = {k: x.get(k) for k in ('slug', 'en', 'zh', 'ru', 'native', 'aliases', 'category', 'country', 'region', 'kind', 'tier')}
            blocks.append(f"## TEA {x['slug']}\n" + json.dumps(row, ensure_ascii=False) + "\n\n### DOSSIER\n" + (dossier_text(x['slug']) or '(no dossier)') + "\n")
        with open(os.path.join(BATCH, bid + '.md'), 'w', encoding='utf-8') as out:
            out.write('\n\n'.join(blocks))
        with open(os.path.join(BATCH, bid + '.slugs'), 'w', encoding='utf-8') as out:
            out.write(' '.join(x['slug'] for x in chunk))
        ids.append(bid)
    print(json.dumps(ids))

def main():
    if sys.argv[1] == 'new':  # make_batches.py new 1,2 8 n-t12
        return new_batches({int(t) for t in sys.argv[2].split(',')}, int(sys.argv[3]), sys.argv[4])
    size = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    ids = []
    for group in sorted(os.listdir(ROOT)):
        d = os.path.join(ROOT, group)
        if not os.path.isdir(d):
            continue
        notes = reviews(group)
        todo = []
        for f in sorted(glob.glob(os.path.join(d, '*.json'))):
            if os.path.basename(f).startswith('_'):
                continue
            c = json.load(open(f, encoding='utf-8'))
            if not c.get('history') and os.path.exists(os.path.join(HERE, 'dossier', c['slug'] + '.json')):
                todo.append(f)
        for i in range(0, len(todo), size):
            bid = f"e-{group}-{i // size:02d}"
            with open(os.path.join(BATCH, bid + '.md'), 'w', encoding='utf-8') as out:
                out.write('\n\n'.join(card_block(f, notes) for f in todo[i:i + size]))
            ids.append(bid)
    print(json.dumps(ids))

main()
