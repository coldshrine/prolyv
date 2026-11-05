#!/usr/bin/env python3
"""Find free-licence photo candidates for each tea on Wikimedia and build one contact sheet per tea for visual checking.
Usage: photo_candidates.py [slug-list.json]   (default: every card without an `images` field)
Writes photos/<slug>.json and photos/sheets/<slug>.jpg. Licences kept: CC0, public domain, CC BY, CC BY-SA. No shop photos, nothing non-free."""
import concurrent.futures as cf, glob, hashlib, io, json, os, re, sys, threading, time, unicodedata
import requests
from PIL import Image, ImageDraw, ImageOps
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import wmgate
ROOT = os.path.join(HERE, '..', 'teas')
OUT = os.path.join(HERE, 'photos'); SHEETS = os.path.join(OUT, 'sheets'); CACHE = os.path.join(HERE, 'raw', 'photo-cache')
for d in (OUT, SHEETS, CACHE):
    os.makedirs(d, exist_ok=True)
S = requests.Session()
S.headers['User-Agent'] = 'ProlyvTeaResearch/0.1 (tea encyclopedia research; https://github.com/coldshrine) python-requests'
GATE = threading.BoundedSemaphore(3)
COMMONS = 'https://commons.wikimedia.org/w/api.php'
FREE = re.compile(r'^(cc0|cc[- ]?zero|public domain|pd\b|pd-|cc[- ]by(-sa)?([- ]\d(\.\d)?)?([- ][a-z]{2,3}(-[a-z]+)?)?$)', re.I)
BAD = ('flag', ' map', 'map.', 'icon', 'logo', 'symbol', 'coat of arms', 'seal of', 'locator', 'svg', 'chart', 'diagram', 'stamp', 'banknote', 'portrait', 'wikipedia', 'commons-', 'ambox', 'book', 'manuscript', 'cover')
EXT = ('.jpg', '.jpeg', '.png', '.webp')

class Limited(Exception):
    pass

def get(url, params=None, raw=False):
    key = hashlib.sha1((url + json.dumps(params or {}, sort_keys=True)).encode()).hexdigest()
    path = os.path.join(CACHE, key)
    if os.path.exists(path):
        data = open(path, 'rb').read()
        if not data:
            return None
        return data if raw else json.loads(data)
    limited = False
    for attempt in range(8):
        try:
            wmgate.wait()
            with GATE:
                r = S.get(url, params=params, timeout=30)
            if r.status_code in (429, 503):
                limited = True
                wmgate.pause(int(r.headers.get('retry-after') or 0) or 5 * 2 ** attempt)
                continue
            limited = False
            if r.status_code != 200:
                open(path, 'wb').close(); return None
            open(path, 'wb').write(r.content)
            return r.content if raw else r.json()
        except (requests.RequestException, ValueError):
            time.sleep(1 + attempt)
    if limited:
        raise Limited(url)
    return None

def strip(v):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', v or '')).strip()

def ok_name(title):
    n = title.lower()
    return n.endswith(EXT) and not any(b in n for b in BAD)

def page_files(lang, title):
    d = get(f'https://{lang}.wikipedia.org/w/api.php', dict(action='query', prop='images', imlimit=40, titles=title, format='json', redirects=1))
    try:
        pages = d['query']['pages']
    except (TypeError, KeyError):
        return []
    out = []
    for p in pages.values():
        out += [i['title'].replace('Image:', 'File:').replace('Datei:', 'File:') for i in p.get('images', [])]
    # local names like 文件: or Файл: are normalised to File:
    return ['File:' + t.split(':', 1)[1] for t in out if ':' in t]

def wikidata_files(qid):
    d = get('https://www.wikidata.org/w/api.php', dict(action='wbgetclaims', entity=qid, format='json'))
    claims = (d or {}).get('claims', {})
    files = ['File:' + c['mainsnak']['datavalue']['value'] for c in claims.get('P18', []) if c['mainsnak'].get('datavalue')]
    cats = [c['mainsnak']['datavalue']['value'] for c in claims.get('P373', []) if c['mainsnak'].get('datavalue')]
    return files, cats

def category_files(cat):
    d = get(COMMONS, dict(action='query', list='categorymembers', cmtitle='Category:' + cat, cmtype='file', cmlimit=25, format='json'))
    try:
        return [m['title'] for m in d['query']['categorymembers']]
    except (TypeError, KeyError):
        return []

def search_files(term, n=8):
    d = get(COMMONS, dict(action='query', list='search', srsearch=f'"{term}" filetype:bitmap', srnamespace=6, srlimit=n, format='json'))
    try:
        return [h['title'] for h in d['query']['search']]
    except (TypeError, KeyError):
        return []

def imageinfo(titles):
    out = []
    for i in range(0, len(titles), 40):
        d = get(COMMONS, dict(action='query', prop='imageinfo', iiprop='url|size|mime|extmetadata', iiurlwidth=330, iiextmetadatafilter='LicenseShortName|LicenseUrl|Artist|ImageDescription|ObjectName', iiextmetadatalanguage='en', titles='|'.join(titles[i:i + 40]), format='json'))
        try:
            pages = d['query']['pages']
        except (TypeError, KeyError):
            continue
        for p in pages.values():
            info = (p.get('imageinfo') or [None])[0]
            if not info or not (info.get('mime') or '').startswith(('image/jpeg', 'image/png', 'image/webp')):
                continue
            meta = info.get('extmetadata') or {}
            lic = strip((meta.get('LicenseShortName') or {}).get('value'))
            if not FREE.search(lic) or (info.get('width') or 0) < 500 or (info.get('height') or 0) < 350:
                continue
            thumb = info.get('thumburl') or info['url']
            big = thumb.replace('/330px-', '/1280px-') if (info.get('width') or 0) > 1280 and '/330px-' in thumb else info['url']
            out.append({'title': p['title'], 'url': big, 'thumb_url': thumb, 'source_page': info.get('descriptionurl'), 'author': strip((meta.get('Artist') or {}).get('value'))[:160], 'license': lic, 'license_url': strip((meta.get('LicenseUrl') or {}).get('value')) or None, 'desc': strip((meta.get('ImageDescription') or {}).get('value'))[:220], 'size': f"{info.get('width')}x{info.get('height')}"})
    return out

def sheet(slug, cands):
    tile = 300
    cols = min(4, len(cands)) or 1
    rows = (len(cands) + cols - 1) // cols
    canvas = Image.new('RGB', (cols * tile, rows * tile), (24, 24, 32))
    draw = ImageDraw.Draw(canvas)
    kept = []
    for c in cands:
        raw = get(c['thumb_url'], raw=True)
        if not raw:
            continue
        try:
            with Image.open(io.BytesIO(raw)) as im:
                pic = ImageOps.exif_transpose(im).convert('RGB')
                pic.thumbnail((tile - 8, tile - 8))
        except Exception:
            continue
        n = len(kept)
        x, y = (n % cols) * tile + 4, (n // cols) * tile + 4
        canvas.paste(pic, (x, y))
        draw.rectangle((x, y, x + 34, y + 26), fill=(0, 0, 0))
        draw.text((x + 8, y + 7), str(n + 1), fill=(255, 255, 0))
        kept.append({**c, 'n': n + 1})
    if kept:
        canvas.save(os.path.join(SHEETS, slug + '.jpg'), 'JPEG', quality=72)
    return kept

def norm(text):
    text = unicodedata.normalize('NFKD', text or '').lower()
    return ''.join(ch for ch in text if ch.isalnum() and not unicodedata.combining(ch))

def name_forms(card):
    """Compact forms of every name of this tea, to tell a photo of it from a photo that only sits on a page about tea in general."""
    raw = [card.get('name_en'), card.get('name_zh'), card.get('name_native'), card.get('name_romanized'), *(card.get('aliases') or [])]
    forms = set()
    for name in raw:
        if not name:
            continue
        for variant in (name, re.sub(r'\s*\(.*?\)', '', name)):
            n = norm(variant)
            n = re.sub(r'(tea|cha)$', '', n) if len(n) > 7 else n
            if len(n) >= 5 or (len(n) >= 2 and re.search(r'[\u3040-\u9fff\uac00-\ud7af]', n)):
                forms.add(n)
    return forms

def gather(card):
    slug = card['slug']
    path = os.path.join(OUT, slug + '.json')
    if os.path.exists(path) and not os.environ.get('REDO'):
        return slug, -1
    dossier_path = os.path.join(HERE, 'dossier', slug + '.json')
    d = json.load(open(dossier_path, encoding='utf-8')) if os.path.exists(dossier_path) else {}
    forms = name_forms(card)
    ranked = {}  # title -> source rank, lower is better
    def add(titles, rank):
        for t in titles:
            if ok_name(t) and t not in ranked:
                ranked[t] = rank
    wd = d.get('wikidata')
    if wd:
        files, cats = wikidata_files(wd['qid'])
        add(files, 0)
        for cat in cats[:1]:
            # a category named after this tea vouches for its files; a broad one like "Green tea" does not
            add(category_files(cat), 1 if any(f in norm(cat) for f in forms) else 5)
    terms = [re.sub(r'\s*\(.*?\)', '', card.get('name_en') or ''), card.get('name_zh'), card.get('name_native'), card.get('name_romanized')]
    compact = re.sub(r'[^A-Za-z]', '', terms[0] or '')
    if card.get('popularity', 1) >= 3:
        terms += [compact] + [a for a in (card.get('aliases') or [])[:3] if re.search(r'[A-Za-z\u4e00-\u9fff]', a)]
    for term in dict.fromkeys(t for t in terms if t and len(t) >= 2):
        add(search_files(term), 2)
    # images in an article: only worth keeping when the file itself names this tea, articles about tea in general have dozens
    for p in (d.get('pages') or [])[:2]:
        if p['title'] in ('Tea', '茶', 'Чай', 'Green tea', 'Black tea', '茶叶'):
            continue
        add([t for t in page_files(p['lang'], p['title']) if any(f in norm(t) for f in forms)], 3)
    titles = sorted(ranked, key=lambda t: (0 if any(f in norm(t) for f in forms) else 1, ranked[t]))[:24]
    info = imageinfo(titles) if titles else []
    def score(c):
        text = norm(c['title'] + ' ' + c.get('desc', ''))
        return (0 if any(f in text for f in forms) else 1, ranked.get(c['title'], 9))
    cands = [c for c in sorted(info, key=score) if score(c)[0] == 0 or score(c)[1] <= 1][:8]
    kept = sheet(slug, cands) if cands else []
    json.dump({'slug': slug, 'candidates': kept}, open(path, 'w', encoding='utf-8'), ensure_ascii=False)
    return slug, len(kept)

def main():
    cards = []
    want = set(json.load(open(sys.argv[1]))) if len(sys.argv) > 1 else None
    for f in glob.glob(os.path.join(ROOT, '*', '*.json')):
        if os.path.basename(f).startswith('_'):
            continue
        c = json.load(open(f, encoding='utf-8'))
        if (want is None and 'images' not in c) or (want and c['slug'] in want):
            cards.append(c)
    cards.sort(key=lambda c: -(c.get('popularity') or 1))
    done = have = 0
    with cf.ThreadPoolExecutor(int(os.environ.get('WORKERS', '4'))) as ex:
        futs = {ex.submit(gather, c): c['slug'] for c in cards}
        for fu in cf.as_completed(futs):
            try:
                _, n = fu.result()
            except Exception as e:
                print('ERR', futs[fu], repr(e)[:160], flush=True); continue
            done += 1
            have += 1 if n > 0 else 0
            if done % 50 == 0:
                print(done, 'of', len(cards), 'with candidates', have, flush=True)
    print('done', done, 'with candidates', have)

if __name__ == '__main__':
    main()
