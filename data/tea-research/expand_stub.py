#!/usr/bin/env python3
"""Turn short rows for rare teas into full cards. Usage: python3 -I expand_stub.py cards/<id>.jsonl
Each row: slug, name_en, name_native, name_romanized, name_zh, name_ru, aliases, category, origin_country, origin_region, cultivar, form, what, history, liquor_hex, leaf_hex, sources, confidence,
optional taste_tags, approach, over {field: text known for this tea}. Or {"slug":..., "skip":"reason"}.
Fields the writer has no facts for get plain text about what is typical of the tea class, worded as typical. Writes data/teas/more-<category>/<slug>.json and validates."""
import glob, json, os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..', 'teas')
THIN = ''
C = {
    'green': dict(processing='Typical of green tea: picked, heated soon after picking to stop oxidation, then shaped and dried.' + THIN,
                  harvest_season='Usually picked in spring, with the earliest pickings prized, as with most green teas.' + THIN,
                  appearance_dry='Green leaf whose shape depends on the maker, as is usual for regional green teas.',
                  appearance_liquor='Usually a clear, pale yellow-green.', appearance_wet='Usually soft green leaf that opens up in the water.',
                  taste_profile='Expect what green teas usually give: fresh, vegetal and lightly sweet.' + THIN, taste_tags=['vegetal', 'sweet'],
                  brewing=dict(grams=3, water_ml=150, temp_c=80, steeps=3, seconds=[60, 75, 105], notes='A safe starting point for green tea: cooler water, short steeps, then adjust by taste.'), caffeine_level='medium',
                  vintage_note="Green teas like this are normally sold as the current year's harvest and are best within a year of picking. It is not an ageing tea.",
                  approach='Treat it as you would any green tea: cooler water, short steeps, and a seller who can say where and when it was picked.'),
    'white': dict(processing='Typical of white tea: withered for a long time and dried with little handling.' + THIN,
                  harvest_season='Usually picked in spring, as with most white teas.' + THIN,
                  appearance_dry='Usually downy buds or soft, silvery-green leaves.', appearance_liquor='Usually a pale straw to light amber.', appearance_wet='Usually soft, intact leaf and bud.',
                  taste_profile='Expect what white teas usually give: gentle, sweet and lightly floral.' + THIN, taste_tags=['sweet', 'floral'],
                  brewing=dict(grams=4, water_ml=120, temp_c=85, steeps=4, seconds=[45, 60, 75, 90], notes='A safe starting point for white tea: use hot but not boiling water and give it time.'), caffeine_level='low',
                  vintage_note='White teas are normally sold either fresh, from the current year, or pressed and aged for several years, with the year stated.',
                  approach='Treat it as you would any white tea: hot but not boiling water, patient steeps, and a seller who can say where and when it was picked.'),
    'yellow': dict(processing="Typical of yellow tea: made like a green tea with an added slow 'smothering' step that mellows the leaf." + THIN,
                   harvest_season='Usually picked in early spring, as with most yellow teas.' + THIN,
                   appearance_dry='Usually yellow-green buds or leaves.', appearance_liquor='Usually a clear, soft yellow.', appearance_wet='Usually soft yellow-green leaf.',
                   taste_profile='Expect what yellow teas usually give: mellow and sweet, softer than a green tea.' + THIN, taste_tags=['sweet', 'vegetal'],
                   brewing=dict(grams=3, water_ml=150, temp_c=80, steeps=3, seconds=[60, 75, 105], notes='A safe starting point for yellow tea: treat it like a delicate green tea.'), caffeine_level='medium',
                   vintage_note="Yellow teas are normally sold as the current year's spring harvest and are best fresh.",
                   approach='Treat it as you would any yellow tea: cooler water, short steeps, and a seller who can say where and when it was picked.'),
    'oolong': dict(processing='Typical of oolong: withered, tossed to bruise the leaf edges, partly oxidised, then fixed, rolled and dried, sometimes roasted.' + THIN,
                   harvest_season='Usually picked in spring or autumn, as with most oolongs.' + THIN,
                   appearance_dry='Usually twisted strips or rolled balls, from green to dark brown depending on the roast.', appearance_liquor='Usually from golden to amber, depending on oxidation and roast.', appearance_wet='Usually whole leaves that open up, often with darker edges.',
                   taste_profile='Expect what oolongs usually give: floral or fruity notes, more roasted when the leaf is roasted.' + THIN, taste_tags=['floral', 'roasted'],
                   brewing=dict(grams=6, water_ml=110, temp_c=95, steeps=6, seconds=[20, 15, 20, 30, 45, 60], notes='A safe starting point for oolong: gongfu style with water just off the boil.'), caffeine_level='medium',
                   vintage_note='Oolongs are normally sold by season and year: light styles are best fresh, roasted styles are often rested for a year or more after roasting.',
                   approach='Treat it as you would any oolong of its region: hot water, short steeps in a small pot, and a seller who can say which season it comes from.'),
    'black': dict(processing='Typical of black tea: withered, rolled, fully oxidised and dried.' + THIN,
                  harvest_season='Picked through the growing season, as with most black teas.' + THIN,
                  appearance_dry='Usually dark twisted leaf, sometimes with golden tips.', appearance_liquor='Usually a clear red-brown.', appearance_wet='Usually coppery brown leaf.',
                  taste_profile='Expect what black teas usually give: malty, sweet and brisk.' + THIN, taste_tags=['malty', 'sweet'],
                  brewing=dict(grams=3, water_ml=200, temp_c=95, steeps=2, seconds=[180, 240], notes='A safe starting point for black tea: Western style, water just off the boil.'), caffeine_level='high',
                  vintage_note='Black teas are normally sold by harvest year and are best within a couple of years.',
                  approach='Treat it as you would any black tea: water just off the boil, a few minutes in the pot, and a seller who can say where it was grown.'),
    'sheng': dict(processing='Typical of raw pu-erh: sun-dried large-leaf mao cha, usually steamed and stone-pressed, then left to age slowly.' + THIN,
                  harvest_season='Usually spring leaf is prized, as with most raw pu-erh.' + THIN, form='cake',
                  appearance_dry='Usually a pressed cake of silvery-green to brown leaf, darkening with age.', appearance_liquor='Usually pale gold when young, deepening to amber with age.', appearance_wet='Usually thick, large leaf.',
                  taste_profile='Expect what young raw pu-erh usually gives: bright, bitter-sweet and full, mellowing with age.' + THIN, taste_tags=['bitter', 'sweet'],
                  brewing=dict(grams=6, water_ml=110, temp_c=95, steeps=7, seconds=[10, 10, 15, 20, 25, 35, 50], notes='A safe starting point for raw pu-erh: rinse once, gongfu style, short early steeps.'), caffeine_level='high',
                  vintage_note='Raw pu-erh is sold by harvest year: young at 1 to 3 years, mid-aged at 5 to 10, aged at 15 or more. Storage matters as much as age.',
                  approach='Treat it as you would any raw pu-erh: rinse the leaf, keep early steeps short, and ask which year and which trees it comes from, since village names on Yunnan teas are often stretched.'),
    'shu': dict(processing='Typical of ripe pu-erh: mao cha is piled, kept damp and fermented for weeks, then dried and often pressed.' + THIN,
                harvest_season='Made from mao cha of various pickings; the season matters less than the fermentation.', form='cake',
                appearance_dry='Usually dark brown leaf, loose or pressed.', appearance_liquor='Usually a dark red-brown.', appearance_wet='Usually soft dark brown leaf.',
                taste_profile='Expect what ripe pu-erh usually gives: smooth, earthy and sweet.' + THIN, taste_tags=['earthy', 'sweet'],
                brewing=dict(grams=6, water_ml=110, temp_c=100, steeps=7, seconds=[10, 10, 15, 20, 30, 40, 60], notes='A safe starting point for ripe pu-erh: rinse once or twice, boiling water.'), caffeine_level='medium',
                vintage_note='Ripe pu-erh is sold by production year, usually drunk at 1 to 10 years; the first year or two often still carries a fermentation smell.',
                approach='Treat it as you would any ripe pu-erh: rinse it, use boiling water, and let a young cake air out if it smells of the fermentation pile.'),
    'heicha': dict(processing='Typical of dark tea: green leaf is piled so that microbes ferment it, then dried and often pressed.' + THIN,
                   harvest_season='Often made from coarser, later leaf, as with most dark teas.' + THIN, form='other',
                   appearance_dry='Usually dark leaf, loose or pressed into bricks or baskets.', appearance_liquor='Usually orange-red to dark brown.', appearance_wet='Usually coarse dark leaf with some stem.',
                   taste_profile='Expect what dark teas usually give: smooth, woody and earthy.' + THIN, taste_tags=['earthy', 'woody'],
                   brewing=dict(grams=6, water_ml=120, temp_c=100, steeps=6, seconds=[15, 15, 20, 30, 45, 60], notes='A safe starting point for dark tea: rinse once, boiling water, or simmer it the traditional way.'), caffeine_level='medium',
                   vintage_note='Dark teas are sold by production year and many improve with several years of dry storage.',
                   approach='Treat it as you would any dark tea: rinse it, use boiling water, and buy from someone who can say who pressed it and when.'),
    'tisane': dict(processing='Dried plant material, as with most herbal infusions.' + THIN,
                   harvest_season='Depends on the plant and the place.' + THIN,
                   appearance_dry='Dried plant material.', appearance_liquor='Depends on the plant; usually pale to amber.', appearance_wet='Softened plant material.',
                   taste_profile='A herbal infusion; the taste depends on the plant.', taste_tags=['herbal'],
                   brewing=dict(grams=2, water_ml=200, temp_c=100, steeps=1, seconds=[300], notes='A common starting point for herbal infusions: boiling water, about five minutes.'), caffeine_level='none',
                   vintage_note='Herbal infusions are not sold by vintage: buy recent stock and use it within a year or so.',
                   approach='Brew it as you would other herbal infusions. If the plant is new to you, check that it is safe for you first, especially when pregnant or on medication.'),
}
KIND = {x['slug']: x.get('kind') for x in json.load(open(os.path.join(HERE, 'logs', 'new-cards.json'), encoding='utf-8'))}
LABEL = dict(green='green', white='white', yellow='yellow', oolong='oolong', black='black', sheng='raw pu-erh', shu='ripe pu-erh', heicha='dark', tisane='herbal')
existing = {os.path.basename(f)[:-5]: f for f in glob.glob(os.path.join(ROOT, '*', '*.json')) if not os.path.basename(f).startswith('_')}
touched, errors = [], 0
for n, line in enumerate(open(sys.argv[1], encoding='utf-8'), 1):
    if not line.strip():
        continue
    try:
        r = json.loads(line)
    except ValueError as e:
        print(f'line {n}: invalid JSON: {e}'); errors += 1; continue
    if r.get('skip'):
        with open(os.path.join(HERE, 'logs', 'skipped.jsonl'), 'a', encoding='utf-8') as log:
            log.write(json.dumps({'slug': r.get('slug'), 'reason': r['skip'], 'file': os.path.basename(sys.argv[1])}, ensure_ascii=False) + '\n')
        continue
    slug, cat = r.get('slug'), r.get('category')
    if cat not in C or not slug or not r.get('what') or not r.get('name_en'):
        print(f'line {n}: needs slug, name_en, what and a valid category'); errors += 1; continue
    folder = os.path.join(ROOT, f'more-{cat}')
    path = os.path.join(folder, slug + '.json')
    if slug in existing and existing[slug] != path:
        print(f'line {n}: slug {slug} already exists at {os.path.relpath(existing[slug], ROOT)}'); errors += 1; continue
    t = dict(C[cat])
    over = r.get('over') or {}
    kind = KIND.get(slug)
    if kind == 'cultivar':
        t['approach'] = f'This is a tea plant cultivar rather than a finished tea, so you are more likely to meet its name in a grower\'s notes than on a packet. If you find tea made from it, brew it like any other {LABEL[cat]} tea and taste what the plant brings.'
        t['processing'] = f'A plant cultivar, not a processing style: it is usually made into {LABEL[cat]} tea, and the method is up to the grower.'
        t['harvest_season'] = 'Depends on the place and the grower.'
        t['appearance_dry'] = 'Depends on how the leaf is processed.'
        t['appearance_liquor'] = 'Depends on how the leaf is processed.'
        t['appearance_wet'] = 'Depends on how the leaf is processed.'
        t['taste_profile'] = f'Depends on how the leaf is processed; tea from it usually falls within what {LABEL[cat]} teas give.'
        t['vintage_note'] = f'A cultivar is not sold by vintage. Tea made from it follows the usual pattern for {LABEL[cat]} tea, so ask for the harvest year and the grower.'
    elif kind in ('blend', 'scented') and cat != 'tisane':
        t['approach'] = 'Recipes for a flavoured or scented tea vary from one maker to the next, so read the ingredient list and brew it like its base tea.'
        t['processing'] = f'A {LABEL[cat]} base tea flavoured or scented by the maker; the recipe varies.'
        t['harvest_season'] = 'The base tea can come from any picking, so the season matters less than the recipe.'
        t['vintage_note'] = 'Flavoured and scented teas are not sold by vintage. Buy recent stock and drink it within a year or so, since the aroma fades.'
    elif kind == 'recipe' and cat != 'tisane':
        t['approach'] = 'This is a way of preparing tea more than a tea you buy under one name, so the recipe and the base tea change from one maker to the next.'
        t['processing'] = f'Made from a {LABEL[cat]} base tea according to the recipe; the method varies.'
        t['harvest_season'] = 'Not tied to a harvest: it depends on the base tea.'
        t['vintage_note'] = 'Not sold by vintage: it is made from whatever base tea the maker keeps, so freshness of the base matters more than the year.'
    card = dict(slug=slug, name_en=r['name_en'], name_native=r.get('name_native'), name_romanized=r.get('name_romanized'), aliases=r.get('aliases') or [], category=cat,
                origin_country=r.get('origin_country'), origin_region=r.get('origin_region') or r.get('origin_country'), cultivar=r.get('cultivar'),
                form=r.get('form') or t.get('form', 'loose'), caffeine_level=r.get('caffeine_level') or t['caffeine_level'], brewing=r.get('brewing') or t['brewing'], taste_tags=r.get('taste_tags') or t['taste_tags'],
                description=r['what'].strip() + '\n\n' + (r.get('approach') or t['approach']), wikipedia_title=r.get('wikipedia_title'), wikipedia_scope=r.get('wikipedia_scope') or 'none', popularity=1,
                name_zh=r.get('name_zh'), name_ru=r.get('name_ru'), history=None if len((r.get('history') or '').strip()) < 40 else r.get('history'), liquor_hex=r.get('liquor_hex'), leaf_hex=r.get('leaf_hex'), sources=r.get('sources') or [], confidence=r.get('confidence') or 'low', images=[], stub=True)
    for k in ('processing', 'harvest_season', 'appearance_dry', 'appearance_liquor', 'appearance_wet', 'taste_profile', 'vintage_note'):
        card[k] = over.get(k) or t[k]
    os.makedirs(folder, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as out:
        json.dump(card, out, ensure_ascii=False, indent=2); out.write('\n')
    touched.append(path)
if touched:
    v = subprocess.run([sys.executable, '-I', os.path.join(HERE, 'validate_cards.py'), '--extended', *touched], capture_output=True, text=True)
    print(v.stdout.strip())
    errors += v.returncode
sys.exit(1 if errors else 0)
