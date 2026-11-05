# Tea card files

One JSON file per tea at `data/teas/<group>/<slug>.json`. `scripts/import_teas.py` reads every file, enriches it from
Wikipedia / Wikidata / Wikimedia Commons (photos, links) and upserts it into the database. Files are the source of
truth for the text; keep them valid JSON (UTF-8, no trailing commas).

## Fields (all required unless marked optional)

| field | type | rules |
|---|---|---|
| `slug` | string | lowercase ascii, words joined by `-`, unique across ALL groups, e.g. `sheng-puerh-yiwu`, `tie-guan-yin-qing-xiang`, `sencha-fukamushi` |
| `name_en` | string | the name an English-speaking tea drinker uses, e.g. `Tie Guan Yin (Qing Xiang)`, `Darjeeling First Flush` |
| `name_native` | string or null | native script: 铁观音, 煎茶, 다즐링… (null when there is none) |
| `name_romanized` | string or null | pinyin with tone marks / rōmaji / revised romanization, e.g. `Tiě Guānyīn` |
| `aliases` | string[] | other spellings and names people search for: `["Iron Goddess of Mercy", "Ti Kuan Yin", "TGY"]`, may be `[]` |
| `category` | enum | `green` `white` `yellow` `oolong` `black` `sheng` `shu` `heicha` `tisane` (`black` = hong cha and Western black; `sheng`/`shu` = raw/ripe pu-erh; `heicha` = other post-fermented dark teas) |
| `origin_country` | string | English country name: `China`, `Taiwan`, `Japan`, `India`, `Sri Lanka`, `Kenya`… |
| `origin_region` | string | province/region, use ` · ` between levels: `Fujian · Wuyi Mountains`, `Yunnan · Xishuangbanna · Yiwu`, `West Bengal · Darjeeling` |
| `cultivar` | string or null | cultivar(s) when meaningful: `Tieguanyin`, `Qingxin`, `Yabukita`, `AV2`… |
| `processing` | string | one or two sentences: oxidation level, rolling, roasting, fermentation, shading… |
| `form` | enum | `loose` (incl. rolled/twisted leaf) `cake` `brick` `tuo` `ball` (dragon balls, pearls) `powder` `stick` `other` |
| `harvest_season` | string | when it is picked and which picking is prized |
| `appearance_dry` | string | one sentence |
| `appearance_liquor` | string | one sentence (colour, clarity) |
| `appearance_wet` | string | one sentence |
| `taste_profile` | string | 1–2 sentences, concrete and sensory |
| `taste_tags` | string[] | 2–5 tags from: `floral` `fruity` `stone-fruit` `citrus` `dried-fruit` `honey` `sweet` `creamy` `nutty` `cocoa` `malty` `roasted` `smoky` `mineral` `grassy` `vegetal` `umami` `marine` `woody` `earthy` `camphor` `medicinal` `spicy` `herbal` `bitter` `brisk` `tart` |
| `brewing` | object | `{"grams": 7, "water_ml": 110, "temp_c": 95, "steeps": 7, "seconds": [15, 10, 15, 20, 30, 45, 60], "notes": "…"}` — gongfu-style unless the tea is normally brewed Western-style or whisked; `seconds` has exactly `steeps` entries |
| `caffeine_level` | enum | `none` `low` `medium` `high` |
| `vintage_note` | string | **the key field**: how and in which years/seasons this tea is normally sold. Examples: sheng pu-erh is sold by harvest year, young 1–3 years, mid-aged 5–10, aged 15+, today's market is mostly 2005–2025; green teas are sold as the current spring harvest only, pre-Qingming lots cost more; Tie Guan Yin is spring and autumn of the current year; Da Hong Pao is the current year or 1–2 years after roasting; white tea is either fresh (current year) or pressed and aged 3–10+ years; shu pu-erh 1–10 years… 2–4 sentences, specific to this tea |
| `description` | string | 3–6 short paragraphs separated by `\n\n`, in the Prolyv voice (see below). Original text, not Wikipedia's |
| `wikipedia_title` | string or null | the English Wikipedia article title for THIS tea if one exists (`Tieguanyin`, `Sencha`, `Darjeeling tea`); otherwise the closest umbrella article (`Pu'er tea`, `Oolong`, `Japanese tea`) |
| `wikipedia_scope` | enum | `exact` when the article is about this very tea, `umbrella` when it is a broader article, `none` when `wikipedia_title` is null |
| `popularity` | int 1–5 | 5 = everyone has heard of it, 1 = connoisseur niche. Drives default ordering |
| `sources_note` | string, optional | anything you were unsure about, for the fact-checker |

## The Prolyv voice

Prolyv is an English-language tea diary and forum run by a tea TikToker. Write like a knowledgeable friend at the tea
table: concrete, warm, no marketing fluff, no mysticism, no "since ancient times". Paragraph plan for `description`:

1. What it is and where it comes from — the one thing that makes it distinct.
2. How it is made and what that does to the leaf and the cup.
3. What it tastes like across steeps, and how to brew it well (what people get wrong).
4. How it is sold and what to look for when buying (grades, seasons, years, common fakes or marketing names).
5. (optional) A practical tip, pairing, or what to try next.

Facts only: no invented awards, prices, exact dates, "the emperor Qianlong…" legends stated as fact (if you mention a
legend, say it is a legend). When unsure of a number, say "typically" or give a range. British/American spelling
either way, be consistent in one card. Never copy sentences from Wikipedia.

## Example

```json
{
  "slug": "tie-guan-yin-qing-xiang",
  "name_en": "Tie Guan Yin (Qing Xiang)",
  "name_native": "清香铁观音",
  "name_romanized": "Qīngxiāng Tiě Guānyīn",
  "aliases": ["Iron Goddess of Mercy", "Ti Kuan Yin", "TGY", "Anxi Tieguanyin", "Green-style Tieguanyin"],
  "category": "oolong",
  "origin_country": "China",
  "origin_region": "Fujian · Anxi",
  "cultivar": "Tieguanyin",
  "processing": "Lightly oxidised (roughly 10–20%), tightly ball-rolled, only lightly dried with no real roast; usually vacuum-packed in small foil bags and kept refrigerated.",
  "form": "loose",
  "harvest_season": "Spring (late April to May) and autumn (October). Autumn lots are often the most fragrant; summer lots are cheaper and flatter.",
  "appearance_dry": "Tight, glossy jade-green pellets, often with a short reddish stem attached.",
  "appearance_liquor": "Pale yellow-green and crystal clear.",
  "appearance_wet": "Large whole leaves unfurl almost completely, bright green with faintly red edges.",
  "taste_profile": "Orchid and lilac florals over a creamy, buttery body, with a long cooling sweetness and only a touch of grip.",
  "taste_tags": ["floral", "creamy", "sweet", "vegetal"],
  "brewing": {"grams": 7, "water_ml": 110, "temp_c": 95, "steeps": 7, "seconds": [15, 10, 15, 20, 30, 45, 60], "notes": "Rinse once for a few seconds to open the pellets. Porcelain gaiwan; keep the first steeps short or the florals turn grassy."},
  "caffeine_level": "medium",
  "vintage_note": "Sold by season: the spring and autumn harvests of the current year, and most of it should be drunk within a year of picking (keep it sealed and cold). It is not an ageing tea in this green style; aged, re-roasted Tieguanyin (chen xiang) is a separate product sold with its age stated.",
  "description": "Tie Guan Yin from Anxi in southern Fujian is the oolong most people meet first, and the modern 'qing xiang' (clear fragrance) style is the reason: it is green, floral and creamy rather than roasted, and it looks like little jade pellets.\n\nThe leaves are oxidised only lightly and then rolled and dried over and over until they curl into balls, with just enough heat to fix the aroma and none of the charcoal roasting the traditional style used. That is why it needs to live in the fridge: the flavour that makes it special is also the first thing to fade.\n\nBrewed gongfu style it opens with orchid and a buttery texture, turns sweeter and softer by the third and fourth steeps, and fades gracefully rather than turning harsh. Most mistakes come from water that is too cool or steeps that are too long; use water just off the boil and keep the early infusions short.\n\nIt is sold by harvest, spring and autumn, in small vacuum packs; a good shop will tell you the season and the village. Avoid anything with a bright, almost neon green colour and a perfume-like smell, which usually means heavy flavouring or very low oxidation chasing colour over taste.\n\nIf you like this and want more depth, try the traditional roasted style or an aged chen xiang Tieguanyin next: same leaf, completely different tea.",
  "wikipedia_title": "Tieguanyin",
  "wikipedia_scope": "exact",
  "popularity": 5
}
```

## Extended fields (encyclopedia, added 2026-11-05)

The user wants a tea Wikipedia: names in Chinese, English and Russian, taste, colour, picture and history for every tea.
These fields are filled by the enrichment pass; a writer creating a new card fills them too.

| field | type | rules |
|---|---|---|
| `name_zh` | string | simplified Chinese. The established Chinese name, also for non-Chinese teas (煎茶, 大吉岭春摘茶, 锡兰高地红茶). No established name: a plain descriptive rendering. Traditional forms go in `aliases` |
| `name_ru` | string | how a Russian-speaking tea drinker writes it. Chinese teas in Palladius transliteration (Те Гуань Инь, Да Хун Пао, Лун Цзин, Шэн Пуэр, Дань Цун); others in the usual Russian form (Сенча, Гёкуро, Дарджилинг). Variant spellings go in `aliases` |
| `history` | string | 1–4 short paragraphs separated by `\n\n`, original text: where and when it is documented, who made it known, how it changed. Legends are labelled as legends. Obscure tea with little documented: 1 honest paragraph saying what is and isn't known. Never invent dates, emperors, awards |
| `liquor_hex` | string | `#RRGGBB`, the typical liquor colour of a good steep |
| `leaf_hex` | string | `#RRGGBB`, the typical dry leaf colour |
| `sources` | string[] | 1–6 URLs you actually opened for this card: Wikipedia (en/zh/ru/ja), Wikidata, government or association pages, reputable tea references. Never a URL you did not open |
| `confidence` | enum | `high` (well documented), `medium` (a few sources agree), `low` (thin evidence, few claims made) |
| `i18n` | object, optional | `{"ru": {...}, "zh": {...}}`, each with `processing`, `harvest_season`, `appearance_dry`, `appearance_liquor`, `appearance_wet`, `taste_profile`, `vintage_note`, `history`, `description`, `brewing_notes`. Added by the translation pass |
| `images` | array, optional | curated photos, added by the photo pass: `{"url", "thumb_url", "source_page", "author", "license", "license_url", "kind", "alt"}`, `kind` is one of `dry_leaf` `liquor` `wet_leaf` `cake` `plant` `plantation` `serving` `other`. Only free licences (CC0, PD, CC BY, CC BY-SA) |
