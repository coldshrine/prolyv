#!/usr/bin/env python3
"""Write new tea cards from a writer's JSONL into data/teas/more-<category>/. Usage: python3 -I add_cards.py cards/<id>.jsonl
A slug that already exists in another group is refused, unless it is the same file being rewritten by the same writer. Prints validator problems; exit 1 if any."""
import glob, json, os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..', 'teas')
existing = {os.path.basename(f)[:-5]: f for f in glob.glob(os.path.join(ROOT, '*', '*.json')) if not os.path.basename(f).startswith('_')}
touched, errors = [], 0
for n, line in enumerate(open(sys.argv[1], encoding='utf-8'), 1):
    if not line.strip():
        continue
    try:
        c = json.loads(line)
    except ValueError as e:
        print(f'line {n}: invalid JSON: {e}'); errors += 1; continue
    if c.get('skip'):
        with open(os.path.join(HERE, 'logs', 'skipped.jsonl'), 'a', encoding='utf-8') as log:
            log.write(json.dumps({'slug': c.get('slug'), 'reason': c['skip'], 'file': os.path.basename(sys.argv[1])}, ensure_ascii=False) + '\n')
        continue
    slug, cat = c.get('slug'), c.get('category')
    if not slug or not cat:
        print(f'line {n}: missing slug or category'); errors += 1; continue
    folder = os.path.join(ROOT, f'more-{cat}')
    path = os.path.join(folder, slug + '.json')
    if slug in existing and existing[slug] != path:
        print(f'line {n}: slug {slug} already exists at {os.path.relpath(existing[slug], ROOT)}'); errors += 1; continue
    os.makedirs(folder, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as out:
        json.dump(c, out, ensure_ascii=False, indent=2); out.write('\n')
    touched.append(path)
if touched:
    r = subprocess.run([sys.executable, '-I', os.path.join(HERE, 'validate_cards.py'), '--extended', *touched], capture_output=True, text=True)
    print(r.stdout.strip())
    errors += r.returncode
sys.exit(1 if errors else 0)
