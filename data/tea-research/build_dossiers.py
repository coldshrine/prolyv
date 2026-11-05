#!/usr/bin/env python3
"""Pre-read sources for card writers, so each writer spends its tokens on writing instead of fetching pages one by one.
Usage: build_dossiers.py items.json  (a list of {slug,en,zh,ru,native,aliases,country,evidence,wikipedia_title,wikipedia_scope})
Writes dossier/<slug>.json. Responses are cached in raw/dossier-cache. Only public pages, no sign-in, one polite identified client."""
import concurrent.futures as cf, hashlib, html, json, os, re, sys, threading, time, urllib.parse
import requests
from bs4 import BeautifulSoup
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wmgate

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, 'raw', 'dossier-cache')
OUT = os.path.join(HERE, 'dossier')
os.makedirs(CACHE, exist_ok=True); os.makedirs(OUT, exist_ok=True)
S = requests.Session()
S.headers['User-Agent'] = 'ProlyvTeaResearch/0.1 (tea encyclopedia research; https://github.com/coldshrine) python-requests'
LOCK = threading.Lock()
LIMIT = {'en': 5000, 'zh': 3500, 'ru': 2500, 'ja': 2000, 'ko': 1500, 'vi': 1500}
CUT = re.compile(r'\n==+\s*(References|See also|Notes|External links|Further reading|Bibliography|Sources|参考|参见|参考资料|参考文献|外部链接|外部連結|注释|脚注|相关条目|Примечания|Литература|Ссылки|См\. также|脚注|関連項目|外部リンク|参考文献|각주|참고 문헌|같이 보기|Tham khảo|Chú thích|Liên kết ngoài)\b.*', re.S)
TEA = re.compile(r'tea\b|茶|чай|чая|차|trà', re.I)

class RateLimited(Exception):
    pass

# Wikimedia: at most 3 concurrent requests and 200 per minute for a compliant User-Agent; Retry-After is honoured.
WM_GATE = threading.BoundedSemaphore(3)

def wm_wait():
    wmgate.wait()

def wm_pause(seconds):
    wmgate.pause(seconds)

def get(url, params=None, is_json=True, timeout=20):
    key = hashlib.sha1((url + json.dumps(params or {}, sort_keys=True)).encode()).hexdigest()
    path = os.path.join(CACHE, key)
    if os.path.exists(path):
        raw = open(path, encoding='utf-8').read()
        if not raw:
            return None
        return json.loads(raw) if is_json else raw
    wm = 'wikipedia.org' in url or 'wikidata.org' in url or 'wikimedia.org' in url
    limited = False
    for attempt in range(8 if wm else 3):
        try:
            if wm:
                wm_wait(); WM_GATE.acquire()
            try:
                r = S.get(url, params=params, timeout=timeout)
            finally:
                if wm:
                    WM_GATE.release()
            if r.status_code in (429, 503):
                limited = True
                wait = int(r.headers.get('retry-after') or 0) or 5 * 2 ** attempt
                if wm:
                    wm_pause(wait)
                time.sleep(min(wait, 60) if not wm else 1)
                continue
            limited = False
            if r.status_code != 200:
                open(path, 'w').close(); return None
            if is_json:
                data = r.json(); open(path, 'w', encoding='utf-8').write(json.dumps(data, ensure_ascii=False)); return data
            r.encoding = r.apparent_encoding or r.encoding
            text = r.text; open(path, 'w', encoding='utf-8').write(text); return text
        except (requests.RequestException, ValueError):
            time.sleep(1 + attempt)
    if limited and wm:
        raise RateLimited(url)
    return None

def api(lang, **params):
    params.update(format='json', redirects=1)
    return get(f'https://{lang}.wikipedia.org/w/api.php', params)

PAGES = {}

def page(lang, title):
    key = (lang, title)
    if key not in PAGES:
        PAGES[key] = _page(lang, title)
    return PAGES[key]

def _page(lang, title):
    extra = {'variant': 'zh-cn'} if lang == 'zh' else {}
    d = api(lang, action='query', prop='extracts|pageprops', explaintext=1, titles=title, **extra)
    try:
        p = next(iter(d['query']['pages'].values()))
    except (TypeError, KeyError, StopIteration):
        return None
    if 'missing' in p or not p.get('extract'):
        return None
    pp = p.get('pageprops', {})
    if 'disambiguation' in pp:
        return None
    text = CUT.sub('', p['extract']).strip()
    return {'lang': lang, 'title': p['title'], 'url': f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(p['title'].replace(' ', '_'))}", 'qid': pp.get('wikibase_item'), 'text': text}

def wikidata(qid):
    d = get('https://www.wikidata.org/w/api.php', dict(action='wbgetentities', ids=qid, props='labels|aliases|sitelinks|descriptions', languages='en|zh|zh-hans|zh-cn|zh-tw|ru|ja|ko|vi', sitefilter='enwiki|zhwiki|ruwiki|jawiki|kowiki|viwiki', format='json'))
    try:
        e = d['entities'][qid]
    except (TypeError, KeyError):
        return None
    labels = {k: v['value'] for k, v in e.get('labels', {}).items()}
    aliases = {k: [a['value'] for a in v] for k, v in e.get('aliases', {}).items() if k in ('zh', 'zh-hans', 'zh-cn', 'ru', 'en')}
    links = {k[:-4]: v['title'] for k, v in e.get('sitelinks', {}).items()}
    return {'qid': qid, 'url': f'https://www.wikidata.org/wiki/{qid}', 'labels': labels, 'aliases': aliases, 'sitelinks': links, 'description_en': e.get('descriptions', {}).get('en', {}).get('value')}

def names_of(it):
    out = []
    for n in [it.get('en'), it.get('zh'), it.get('native'), it.get('ru'), *(it.get('aliases') or [])]:
        if n and len(n) >= 3 and n not in out:
            out.append(n)
    return out

def mentions(text, names, limit):
    """Paragraphs that name this tea. Latin names must match on word boundaries so that 'Bei Dou' does not match inside other words."""
    pats = []
    for n in names:
        if re.search(r'[A-Za-z]', n):
            pats.append(r'(?<![A-Za-z])' + re.escape(n) + r'(?![A-Za-z])')
        else:
            pats.append(re.escape(n))
    if not pats:
        return ''
    rx = re.compile('|'.join(pats), re.I)
    got, size = [], 0
    for para in re.split(r'\n+', text):
        para = para.strip()
        if len(para) < 12 or not rx.search(para):
            continue
        para = para[:900]
        got.append(para); size += len(para)
        if size >= limit:
            break
    return '\n'.join(got)

def html_text(raw):
    soup = BeautifulSoup(raw, 'lxml')
    for t in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'form', 'noscript']):
        t.decompose()
    lines = [re.sub(r'\s+', ' ', x).strip() for x in soup.get_text('\n').split('\n')]
    title = soup.title.get_text(strip=True) if soup.title else ''
    return title[:120], '\n'.join(x for x in lines if x)

def search(lang, query, n=3):
    d = api(lang, action='query', list='search', srsearch=query, srlimit=n, srnamespace=0)
    try:
        return [h['title'] for h in d['query']['search']]
    except (TypeError, KeyError):
        return []

PARSED = {}

def build(it):
    slug = it['slug']
    path = os.path.join(OUT, slug + '.json')
    if os.path.exists(path):
        return slug, 'cached'
    names = names_of(it)
    pages, seen = {}, set()
    def add(lang, title):
        if not title or (lang, title) in seen or lang in pages:
            return
        seen.add((lang, title))
        p = page(lang, title)
        if p and TEA.search(p['text'][:4000]):
            pages[lang] = p
    thin = it.get('thin')
    if it.get('wikipedia_scope') == 'exact':
        add('en', it.get('wikipedia_title'))
    elif it.get('wikipedia_scope') is None and not thin:
        add('en', it.get('en'))
        for a in (it.get('aliases') or [])[:2]:
            if 'en' not in pages and re.fullmatch(r'[A-Za-z][A-Za-z \-\']+', a or ''):
                add('en', a)
    add('zh', it.get('zh'))
    if thin and not it.get('zh'):
        add('en', it.get('en'))
    if re.search(r'[一-鿿]', it.get('native') or ''):
        add('zh', it.get('native'))
    if it.get('country') == 'Japan':
        add('ja', it.get('native'))
    if it.get('country') in ('South Korea', 'Korea', 'North Korea'):
        add('ko', it.get('native'))
    if it.get('country') == 'Vietnam':
        add('vi', it.get('en'))
    if not thin and not pages:
        add('ru', it.get('ru'))
    wd = None
    for p in list(pages.values()):
        if p.get('qid'):
            wd = wikidata(p['qid'])
            if wd:
                break
    if wd:
        for lang, title in wd['sitelinks'].items():
            add(lang, title)
    # pages that mention the tea without being about it: umbrella article, then wiki search
    mention_pages = []
    known = {p['title'] for p in pages.values()}
    def add_mention(lang, title):
        if title in known or len(mention_pages) >= 4:
            return
        known.add(title)
        p = page(lang, title)
        if not p:
            return
        m = mentions(p['text'], names, 1100)
        if m:
            mention_pages.append({'lang': lang, 'title': p['title'], 'url': p['url'], 'text': m})
    if it.get('wikipedia_scope') == 'umbrella':
        add_mention('en', it.get('wikipedia_title'))
    for url in it.get('evidence') or []:
        m = re.match(r'https?://(\w+)\.wikipedia\.org/(?:wiki|zh-\w+)/([^?#]+)', url or '')
        if m:
            add_mention(m.group(1), urllib.parse.unquote(m.group(2)).replace('_', ' '))
    if not thin:
        # wiki search only when nothing else turned up: the shared request budget is small, writers can still search the web
        if it.get('zh') and len(it['zh']) >= 2 and not pages and len(mention_pages) < 2:
            for t in search('zh', f'"{it["zh"]}" 茶', 2):
                add_mention('zh', t)
        if it.get('en') and not pages and not mention_pages:
            for t in search('en', f'"{re.sub(r" *[(].*?[)]", "", it["en"])}" tea', 2):
                add_mention('en', t)
    # evidence pages the discovery agents opened
    evidence = []
    for url in [u for u in (it.get('evidence') or []) if isinstance(u, str) and u.startswith('http')][:3]:
        if 'wikipedia.org' in url:
            continue
        cached = PARSED.get(url)
        if cached is None:
            raw = get(url, is_json=False, timeout=15)
            if not raw:
                continue
            try:
                cached = html_text(raw)
            except Exception:
                continue
            if len(PARSED) < 3000:
                PARSED[url] = cached
        title, text = cached
        m = mentions(text, names, 1500)
        if m:
            evidence.append({'url': url, 'title': title, 'text': m})
    out = {'slug': slug, 'wikidata': wd,
           'pages': [{'lang': p['lang'], 'title': p['title'], 'url': p['url'], 'text': p['text'][:LIMIT.get(p['lang'], 1500)]} for p in pages.values()],
           'mentions': mention_pages, 'evidence': evidence}
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False)
    return slug, f"{len(pages)}p {len(mention_pages)}m {len(evidence)}e"

def main():
    items = json.load(open(sys.argv[1], encoding='utf-8'))
    done = thin = 0
    with cf.ThreadPoolExecutor(int(os.environ.get('WORKERS', '8'))) as ex:
        futs = {ex.submit(build, it): it['slug'] for it in items}
        for fu in cf.as_completed(futs):
            try:
                slug, note = fu.result()
            except Exception as e:
                print('ERR', futs[fu], repr(e)[:200], flush=True); continue
            done += 1
            if note.startswith('0p 0m 0e'):
                thin += 1
            if done % 50 == 0:
                print(done, 'of', len(items), 'thin so far', thin, flush=True)
    print('done', done, 'empty dossiers', thin)

if __name__ == '__main__':
    main()
