# Prolyv QA report: developer checklist

Tested on the QA copy at localhost:8100, plus private copies of the frozen code where noted. Viewports were 1440x900 ("desktop") and 375x812 mobile emulation ("phone"). "Both" means both of these.

How to read this:

- Duplicates reported by several testers are merged into one item. A merged item takes the highest severity any tester gave it.
- The tag after each title shows which test areas reported the item.
- "Canvas" names the approved design screen to compare against.
- Design-gap items are large rebuilds. Parking any of them is an owner decision. Only shops, tastings and vendors are parked in the brief.
- Coverage gaps and test data left behind are listed at the end.

---

## Blocker

- [ ] **Telegram callback has no CSRF protection, so an attacker can link their Telegram to a victim's account and take it over, including an admin account** `security`
  - **Page:** `/auth/telegram/callback`, `/settings`
  - **Viewport:** n/a
  - **What to do:** Refuse any callback the user did not start, and never link on a GET callback. In `prolyv/web/routes_auth.py:129-156`, bind the flow to a per-attempt random value (the `telegram.js` fallback can carry a state in `return_to`, or POST with a CSRF token). Linking from `/settings` should require a POST with the session CSRF token. Consider rejecting `Sec-Fetch-Site: cross-site` and storing used Telegram hashes for single use.
  - **Seen:** Step 3 returns 303 to `/settings` with "Telegram is now linked to your account." Step 4 returns 303 to `/`, and the attacker's session is now the victim: `/settings` shows the victim's handle, `/admin` returns 200 "Site admin", and `/settings/export` returns 200. The same flow also does login CSRF on a signed-out victim who has opened `/login`, signing them into the attacker's account. Cross-site top-level GETs carry SameSite=Lax cookies, so the intent cookie does not stop this, although the code comment says it "blocks login CSRF". The payload can be replayed for 24 hours.
  - **Steps:** Needs Telegram login switched on. Reproduced on a private copy with a fake bot token and a self-signed payload standing in for Telegram's signature.
    1. The victim is signed in with no Telegram linked (an email-only user in ADMIN_HANDLES was used) and opens `/settings`. This sets the `prolyv_tg` intent cookie for 30 minutes.
    2. The attacker gets their own valid widget payload (id, first_name, auth_date, hash) and does not complete sign-in with it.
    3. The victim's browser opens `/auth/telegram/callback?<attacker payload>` from an attacker's link, sent with `Referer: https://evil.example` and `Sec-Fetch-Site: cross-site`.
    4. The attacker opens `/login` in their own browser, then the same callback URL.

- [ ] **A session made private, or hidden by an admin, stays publicly visible on any topic or reply it is attached to** `forum` `security`
  - **Page:** `/discussions/{id}`, topic lists
  - **Viewport:** both
  - **What to do:** Show the attached card only if `sess.is_public and not sess.hidden`, or the viewer is the owner or an admin. Otherwise remove it or show a neutral placeholder. `prolyv/templates/forum/topic.html:19` and `:43` render `topic.attached_session` and `r.attached_session` with no check, and `partials/macros.html:51` prints the "session attached" label. `resolve_attachment` only checks at attach time.
  - **Seen:** A signed-out visitor sees the whole card: tea name, grams, ml, temperature, teaware, the private note (up to 160 characters), author, rating and a "private" badge. The card links to `/sessions/{id}`, which correctly returns 404. Lists still say "session attached". The result is the same for replies and for sessions hidden through `/admin/hide`.
  - **Steps:**
    1. As any user, create a public session with a note, then a topic with that session attached (`attached_session_id`).
    2. POST `/sessions/{id}/visibility` with `visibility=private` (the Make private button).
    3. Signed out, in a clean cookie jar, open the session page (404), then the topic.
    4. Live examples on 8100: session `b66cd4df-b5a7-41aa-b121-e18bc7b5e567`, topic `92f7fb37-4c79-47a3-9262-95b886dbf28a`.

---

## Major

### Security and data

- [ ] **Admin rights follow the handle string, so anyone who holds a free admin handle becomes admin** `security`
  - **Page:** `/settings`, sign-up, ADMIN_HANDLES
  - **Viewport:** n/a
  - **What to do:** Tie admin to a user id or a verified identity (`users.is_admin` column or admin user ids). `prolyv/config.py:is_admin_handle` is used in `web/deps.py` and auth/sessions callers. At minimum, block ADMIN_HANDLES values in `is_valid_handle`, `generate_handle` and `settings_save` for everyone except an existing admin row.
  - **Seen:** Case A: the new account automatically gets handle `qa_ownerx` and `/admin` returns 200. Case B: karyna loses admin (403) and tea_mike gets `/admin` 200. On production, if ADMIN_HANDLES is set before the owner registers, the first person with that email local part, Telegram username or Google first name becomes admin. The same applies after the admin renames or deletes her account, because deletion frees the handle.
  - **Steps:** On a private copy.
    - A) Set `ADMIN_HANDLES=karyna,qa_ownerx` with no user called qa_ownerx. Sign up by magic link as `qa_ownerx@example.test`, then GET `/admin`.
    - B) As karyna, change the handle to `karyna_new`. As tea_mike, POST `/settings` with `handle=karyna`, then GET `/admin`.

- [ ] **A 156 KB PNG of about 50 megapixels takes the server to about 900 MB, and any signed-in user can send it** `security`
  - **Page:** `/sessions/new`, `/sessions/{id}/edit` (photo upload)
  - **Viewport:** n/a
  - **What to do:** Keep upload memory well under the service limit (`deploy/prolyv.service` sets MemoryMax=300M on a 956 MB VM with one worker). In `prolyv/uploads.py`, lower MAX_PIXELS (for example to 20 MP), use `Image.draft` or `thumbnail` before `convert`, and drop the full-size copies (upright, clean, large). Run `save_session_photo` in a thread (`await run_in_threadpool`). Add `post_quota` to session create and edit.
  - **Seen:** The request returns 303 and peak RSS reaches 905 MB with one photo. With four photos it was 872 MB, and `/healthz` stalled for 1.0 s because decoding runs on the event loop. Memory was still 872 MB after the request. The file passes the 10 MB and MAX_PIXELS checks. Session creation has no quota (40 of 40 passed in 0.2 s). By these numbers the systemd limit should kill the only worker. This was measured on macOS, not on the VM.
  - **Steps:** On a private copy, freshly restarted (RSS 101 MB). Create an 8000x6200 solid-colour PNG (156,209 bytes). Upload it as one photo in POST `/sessions/new` while sampling the process RSS.

- [ ] **Photos of private sessions are served to anyone with the URL, and stay reachable after a public session goes private** `security` `diary`
  - **Page:** `/media/sessions/...`
  - **Viewport:** n/a
  - **What to do:** Refuse a private session's photos for everyone except the owner and admins, like the session page. `prolyv/app.py:78` mounts `/media` as plain StaticFiles. Serve session photos through a route that calls `get_session`, or move the files or sign the URLs when visibility changes. Add `Cache-Control: private`.
  - **Seen:** 200 `image/webp` in every case, with no auth check and no Cache-Control. The path uses uuid4, so a stranger needs the link, but anyone who saw the session while public or received a link keeps access. Photos are removed on session delete (404).
  - **Steps:** Create a public session with a photo and copy the photo URL. Make the session private. Signed out, GET `/media/sessions/b66cd4dfb5a741aab121e18bc7b5e567/5456455aee5b458e9241a9a93033fbec-1200.webp` on 8100. Also try a session that was private from the start, signed out and as another user (done on a private copy).

- [ ] **The cached story PNG stays public after the session is made private, deleted, or the whole account is deleted** `security`
  - **Page:** `/media/stories/...`
  - **Viewport:** n/a
  - **What to do:** Remove or refuse the cached image once the session stops being public, and always remove it on session or account deletion. In `prolyv/web/routes_diary.py:338-347` and `routes_account.py:273`, also delete `stories/<hex>-*.png` (`delete_session_media` only removes `sessions/<hex>`). In `session_visibility`, delete the cached file when the session goes private. Caching stories outside the public `/media` mount would be better.
  - **Seen:** `/sessions/{id}/story.png` correctly returns 404 after going private. The cached file still returned 200 after private, after session delete and after account delete, when `/u/olena_tea` and the session page were both 404. The filename is only the session id plus a unix timestamp in seconds, and `/media` has no rate limit.
  - **Steps:** On a private copy.
    1. Make a session public and GET `/sessions/{id}/story.png`, which writes `media/stories/<id hex>-<updated_at ts>.png`.
    2. Set the session private, delete it, or delete the account through `/settings/delete`.
    3. Signed out, GET `/media/stories/<file>`.

### Site-wide

- [ ] **One long unbroken string or URL makes the whole page scroll sideways and pushes the phone tab bar off screen** `shell-feed` `diary` `forum` `blog` `people` `tea-base` `admin-static`
  - **Page:** `/` (all tabs), `/diary`, `/discussions`, `/discussions/s/{slug}`, `/discussions/{id}`, `/saved`, `/sessions/{id}`, `/blog/{slug}`, `/u/{handle}`, `/teas/{slug}`, `/teas?q=`, `/search?q=`, `/admin/reports`
  - **Viewport:** both
  - **What to do:** Wrap long words and URLs inside their cards, with no page-level horizontal scroll, and keep the tab bar fixed on screen.
    - Add `overflow-wrap:anywhere` to `.session-title`, `.session-note`, `.topic-title`, `.topic-excerpt`, `.user-text`, `blockquote`, `.comment p`, `.steep-note`, `.display`, `.profile-line`, `.prose`/`.post-prose`, `.request-list`, tea description text, `.h1` and `.empty-title`.
    - Add `min-width:0` on their flex and grid parents, including `.profile-meta`.
    - Truncate or wrap the "Ask ... in the forum" button label.
    - Wrap Markdown tables in an `overflow-x:auto` container.
    - `app.css` currently sets this only on `.provider-detail` and `.sources` (line 186).
  - **Seen:** scrollWidth by page:
    - `/?tab=discussions` phone: 466, tab bar at y=926 on an 812 screen. A pasted link in one topic breaks the home page for every phone visitor until it drops off the feed.
    - `/` phone: 1751, tab bar 1500 wide at y=3164. `/` desktop: 1851 to 2825.
    - `/diary` phone: 1751. `/sessions/{id}` phone with an unbroken name and note: 14467, h1 cut off.
    - Session comment of 2000 characters: 18413 desktop, 18313 phone. Any commenter can do this to someone else's session.
    - `/discussions` and section list: 2833 desktop, 2725 phone with an unbroken title and body. A 150-character URL gives 496 on the thread and 467 on the section list. A 20000-character unbroken body reached 201664.
    - `/blog/{slug}` comment of 360 characters: 2908 phone, 3268 desktop. Long word in the Markdown body alone: 1503 desktop. 180-character URL in a blog body: 1801 phone. A 6-column table reaches 624 wide; the seeded 4-column guide table fits.
    - Tea card description with the same URL: 1692 phone. `/admin/reports` after that topic is reported: 1081 phone.
    - `/u/{handle}` with a 50-character name and 280-character bio: 3614 desktop, 2524 phone, and the "Ask" button is about 926 px wide on the phone.
    - `/teas?q=` with 100 characters: 1870 phone. `/search?q=` with 180 characters: 845 phone. Normal queries are fine.
  - **Steps:** Post a topic, reply or comment containing a 150+ character URL or a long string with no spaces, or use a long no-space session name, profile name or search query. Open the page at 375 and 1440 and compare `documentElement.scrollWidth` with the viewport, and check the `.tabbar` position.

- [ ] **Footer is a single link row instead of the designed multi-column footer, and has no TikTok or Instagram links** `shell-feed` `admin-static`
  - **Page:** Footer on all pages, `/about`
  - **Viewport:** both
  - **What to do:** Build the designed footer: rainbow stripe, logo and tagline/blurb ("the home of our TikTok and Instagram"), four social icons (Threads, TikTok, Instagram, Telegram), columns Community, Forum sections and Project (including Support us and Contact), and the bottom line "© 2026 Prolyv · Made in Ukraine" plus the private-diary line. Add hard links to the profiles on `/about`. Make sure SOCIAL_TIKTOK and SOCIAL_INSTAGRAM are set in production. Canvas: Footer.
  - **Seen:** Logo plus one flat row of 7 to 9 links (Tea base, Discussions, Blog, Saved when signed in, About, Community rules, Privacy, Terms). No tagline, icons, columns, Contact, copyright or visible stripe. Social links only appear when the settings are set (default empty). `/about` says the site grew out of TikTok and Instagram but has 0 links to them.
  - **Steps:** Scroll to the bottom of any page at 1440, signed in and signed out, and compare with Footer.dc.html. Search `/about` for tiktok.com.

- [ ] **Sitemap leaves out the blog, the forum and the static pages** `admin-static`
  - **Page:** `/sitemap.xml`
  - **Viewport:** n/a
  - **What to do:** Extend `sitemap()` in `routes_teas.py` with `/blog`, published posts, `/discussions`, sections, visible topics and static pages. Drop `/login` and the sentence-like origin URLs.
  - **Seen:** 0 matches across about 1,020 URLs. It holds only `/`, `/teas`, `/login`, tea browse pages, 564 `/teas/o/` pages and the tea cards. The five launch guides and all forum topics are absent.
  - **Steps:** `curl /sitemap.xml` and grep for `/blog`, `/discussions`, `/about`, `/rules`, `/privacy`, `/terms`, `/u/`.

### Feed

- [ ] **Feed is capped at 30 items with no Show more, so older content is unreachable** `shell-feed`
  - **Page:** `/`
  - **Viewport:** both
  - **What to do:** `build_feed` uses `limit=30` with no offset. Add pagination and the "Show more" button under the list. Canvas: Feed, MFeed.
  - **Seen:** Exactly 30 cards and no control. A seeded 30 Oct session dropped out of the All feed once newer items existed.
  - **Steps:** Open `/?tab=all`, scroll to the end, count `.feed` children and look for a more or next control.

- [ ] **Feed sidebar blocks are missing, including TikTok and Instagram** `shell-feed`
  - **Page:** `/`
  - **Viewport:** both
  - **What to do:** Desktop: two columns with an aside holding Your diary (Log a tea), Hot topics, Sections chips, and Find us on TikTok and Instagram. Phone: Hot topics and Find us under the list. Canvas: Feed, MFeed.
  - **Seen:** A single full-width column (1192px) on desktop, with none of these blocks on either viewport. No TikTok or Instagram link anywhere on the home page on this copy.
  - **Steps:** Open `/` at 1440 signed in and signed out, read the whole page, then repeat at 375.

- [ ] **Signed-out hero departs from the design and is misaligned with the content** `shell-feed`
  - **Page:** `/` signed out (alignment also signed in)
  - **Viewport:** both
  - **What to do:** Match the design: eyebrow, 60px headline "Log your tea. Talk through every steep.", "Start your diary" and "How it works", animated Cup at 360px (200px on phone), floating "logged a session" chip, and three pillar cards (Diary, Discussions, Blog) with `id=how`. Align the hero with the feed's left edge. Canvas: Feed, MFeed, Cup.
  - **Seen:** Headline "Every steep, in your diary", buttons "Start your diary" and "Browse 441 teas", no cup, chip, pillars or How it works, and a non-canvas "Explore the tea base" promo card. On desktop the hero is a centred 760px column with text at x=348 while the feed starts at x=116.
  - **Steps:** Sign out, open `/`, compare with Feed and MFeed.

- [ ] **Session cards miss the designed structure and are squeezed on the phone** `shell-feed` `people`
  - **Page:** `/` session cards, `/u/{handle}`, `/saved`
  - **Viewport:** both (squeeze on phone)
  - **What to do:** Build the designed card: header row with avatar, author, relative time and SESSION badge; 76px cup (60px on phone) with tea and params; full-width note; footer with stars and comment count; "Brew it the same way" button. Canvas: Feed, MFeed, MProfile.
  - **Seen:** No avatar, time-of-post (date only), SESSION badge or "Brew it the same way". Author is a small @handle under the note. Cup is 56px. On the phone a 90px stars column plus the cup leave 135px of a 343px card for text, so notes wrap every 2 to 4 words.
  - **Steps:** Open `/?tab=sessions`, or `/?tab=following` after following someone, at 1440 and 375. Also open `/u/prolyv` or `/u/olena_tea` at 375.

- [ ] **Blog cards in the feed lose the cover treatment, and a cover makes the card huge** `shell-feed`
  - **Page:** `/` blog cards
  - **Viewport:** both
  - **What to do:** Use a side cover about 200px wide (cup on a gradient, with a badge), "BLOG · 4 MIN", title, excerpt and "Read and discuss →". On phone, a 150px cover on top. `.post-cover img` is `width:100%` with aspect-ratio 16/9: use a side cover or fixed height in the feed, plus a cup fallback. Canvas: Feed, MFeed.
  - **Seen:** Posts without a cover are plain text cards 107px tall, with no cup, gradient, badge, read time or "Read and discuss →". A post with a cover becomes 1192x776 on desktop.
  - **Steps:** Open `/?tab=blog` at 1440 and measure a card without a cover and one with a cover.

### Diary and sessions

- [ ] **Pressing Enter in the steep "seconds" box saves the whole session and loses the typed steep** `diary`
  - **Page:** `/sessions/new`
  - **Viewport:** both
  - **What to do:** Make Enter add the steep and keep the form open: handle keydown Enter on `#manual-seconds` with `preventDefault`. Consider blocking implicit submit on the other single-line inputs too.
  - **Seen:** The form submits at once and redirects to `/sessions/{id}` with "Session saved to your diary" and Steeps: 0. A half-logged private session now exists.
  - **Steps:** Log a tea, type a tea name, focus the "seconds" box under the timer, type 45, press Enter.

- [ ] **After any validation error, empty Leaf, Water and Temp fields show the word "None", and saving again fails** `diary`
  - **Page:** `/sessions/new` (POST with an error)
  - **Viewport:** both
  - **What to do:** Keep empty fields empty and keep the raw input on error. `parse_session_form` stores None and the template prints it: render `form.grams if form.grams is not none else ''`.
  - **Seen:** The re-rendered form has `value="None"` in all three. Saving again returns 400 with "Leaf must be a number. Water must be a number. Temperature must be a number." until the user clears them by hand. Non-numeric grams ("abc") also becomes "None".
  - **Steps:** Open `/sessions/new`, leave the tea name and params empty, press "Save to diary". Then fill in the tea name only and save again. (First step reproduced via HTTP POST with identical fields; resubmit with grams, water and temp set to "None".)

- [ ] **Draft restore corrupts taste tags: it ticks all 24 or clears all of them** `diary`
  - **Page:** `/sessions/new` (draft autosave)
  - **Viewport:** both
  - **What to do:** In `session.js` `collect()`, store multi-value checkboxes as an array of values and restore by membership.
  - **Seen:** With "thin" ticked, 24 of 24 tags are checked after reload. With "honey" ticked, 0 are checked. The draft stores `taste_tags` as one boolean, the state of the last checkbox.
  - **Steps:** Log a tea, type a name, tick "thin" (the last tag) and reload. Separately, tick only "honey" and reload.

- [ ] **Sessions older than 30 days cannot be reached from the diary** `diary`
  - **Page:** `/diary`
  - **Viewport:** both
  - **What to do:** Add "Show older" or pagination under "Last 30 days", and link the calendar days and Shelf rows to sessions.
  - **Seen:** The session counts in "sessions logged" and is listed in Shelf as plain text ("1× · last 15 Jan"), but there is no card or link. After a month a private entry is reachable only by a saved URL.
  - **Steps:** Log a private session dated 2026-01-15 and open `/diary`.

- [ ] **Shelf rows break into a narrow column, one word per line** `diary`
  - **Page:** `/diary` (Shelf)
  - **Viewport:** both
  - **What to do:** Fix the selector. The CSS targets `.shelf .cup-mark` but the macro renders `span.cup`, so `grid-row 1/3` never applies. The result should be one compact meta line per tea ("1× · last 2 Nov · ★ 3.0").
  - **Seen:** Meta text sits in the 28px cup column and wraps per word. The Shelf card is 1085px tall at 1440 for 6 teas, and the phone page is over 6000px tall.
  - **Steps:** Open `/diary` as demo_user and scroll to Shelf.

- [ ] **Diary screen clearly departs from the approved design** `diary`
  - **Page:** `/diary`
  - **Viewport:** both
  - **What to do:** Build from MyDiary.dc.html and MDiary.dc.html: hero "Hi, [name] / Your last 30 days in cups" with a large spinning cup and "Your favourite type is …"; stat cards with hints (Sessions, Favourite type, Average rating, New teas); "New session" and "Share your month"; "Five weeks in cups" calendar with Mon–Sun headers, a cup per day and date labels; filter chips (All, Oolong, Black, Shu pu-erh, Other) with an empty message; "Share" on each session; "Your tea profile" (type bars, top notes); "Your tea year 2026" card.
  - **Seen:** Hero "My diary / Demo User" with no cup. Stat tiles have no hints and differ. The calendar is a 12-week heatmap of unlabelled squares. No filter chips, per-session Share, Share your month, tea profile or tea-year card. "Running low" is replaced by an all-teas Shelf, which is fine if parked.
  - **Steps:** Compare `/diary` with MyDiary and MDiary.

- [ ] **New session form departs from the design: no live preview, no steppers, no phone top bar** `diary`
  - **Page:** `/sessions/new`
  - **Viewport:** both
  - **What to do:** Build from NewSession.dc.html and MNewSession.dc.html: title "New session", breadcrumb, "Log as you brew — your draft saves automatically."; desktop two-column layout with the sticky "How others will see it" preview; −/+ steppers for Leaf, Water and Temperature; steep list with orbs and "+ Steep · N s"; taste chips in the Taste section; privacy checkbox "Show in the community feed"; phone top bar with Cancel, New session and Save.
  - **Seen:** Single narrow column titled "Log a tea", no breadcrumb or preview, free-text inputs. On the phone "Save to diary" sits at the very end of a page about 2600px long. Privacy is a Private/Public toggle and "Allow comments when public".
  - **Steps:** Compare `/sessions/new` with NewSession and MNewSession.

- [ ] **Session page lacks the design's signature blocks** `diary`
  - **Page:** `/sessions/{id}`
  - **Viewport:** both
  - **What to do:** Build from SessionPost.dc.html and MSessionPost.dc.html: breadcrumbs (Feed / author / tea); large cup with author row and Follow; kicker "SHU PU-ERH · 10 STEEPS"; params grid; interactive "Steep by steep"; comment form with "Attach my session" and "Post"; Reply and Helpful on comments; "Other sessions of this tea" with "Compare with my sessions"; "Discuss in the forum".
  - **Seen:** None of these except a static steep list. Comments have only delete and Report, with a "Comment" button. The owner aside "Your session" and "Story card" are extras not in the design.
  - **Steps:** Compare `/sessions/{id}` with SessionPost and MSessionPost, viewing as owner and as another user.

- [ ] **Story card is missing the design's content: taste tags, tea type label, "my tea of the day"** `diary`
  - **Page:** `/sessions/{id}/story.png`
  - **Viewport:** n/a
  - **What to do:** Build from Story.dc.html: logo with gradient mark and "my tea of the day"; kicker "OOLONG · 8 STEEPS"; title; "by @handle"; stars; tag pills; quoted note; footer with params and site.
  - **Seen:** Plain text "PROLYV · TEA DIARY", no mark, type kicker or tag pills even though tags exist, and the note is not styled as a quote. A "Steeps: 10s · 10s" line is there instead. Handle and site are in the footer. Size is correctly 1080×1920.
  - **Steps:** Log a public session with rating and tags (Da Hong Pao with honey and roasted) and open `story.png`.

### Forum

- [ ] **Forum index is missing the approved sidebar blocks and the Sort control** `forum`
  - **Page:** `/discussions`
  - **Viewport:** both
  - **What to do:** Desktop: two columns with a Sections panel and a right rail ("Ask a better question" with "New topic with a session", "House rules" with 4 rules, "Most helpful this month"), plus a Sort control (Active, Newest, Unanswered, Most helpful). Phone: the same blocks stacked after the list. Canvas: Forum, MForum.
  - **Seen:** A single centred column with section chips on top. Only pinned then latest activity is available. None of these strings exist in the forum templates, so the session-first entry point is gone.
  - **Steps:** Open `/discussions` at 1440 and 375 and compare with Forum.dc.html and MForum.dc.html.

- [ ] **Topic page departs from the design: header actions, opening-post actions and the side column are missing** `forum`
  - **Page:** `/discussions/{id}`
  - **Viewport:** both
  - **What to do:** Add section badge and "14 replies · 312 views" line, author session count, "Follow topic" and "Reply" in the header; Helpful, Quote and Share on the opening post; a per-steep strip on the attached session; "Sessions in this thread" with "Compare side by side", "Related topics" and the "From the blog" card. Canvas: Topic, MTopic.
  - **Seen:** Header has only breadcrumb, optional Pinned or Solved badge, title and author. Actions are just Save and Report. The opening post cannot be voted on or quoted. The attached session is the generic card. The page is one narrow column at 1440 with empty margins.
  - **Steps:** Open a topic (for example the seeded shu pu-erh topic) signed in and compare with Topic.dc.html and MTopic.dc.html.

- [ ] **Long threads have no reply sort, no collapse and no way to reach the reply box from the top** `forum`
  - **Page:** `/discussions/{id}` (long thread)
  - **Viewport:** both
  - **What to do:** Add a Most helpful / Newest toggle above replies with Most helpful first, a "Show 10 more replies" button, and a per-reply Reply action and top Reply button that jump to the form. Canvas: Topic, MTopic.
  - **Seen:** All replies render oldest first, so the helpful reply does not rise. Page height is about 5400px on phone and 4500px on desktop. The only reply box is at the very bottom. Quote does scroll to the form.
  - **Steps:** Open `/discussions/332a37ba-c080-437f-9e5e-266f0d07dccc` (about 23 replies) on desktop and phone.

### Blog

- [ ] **TikTok embed never loads because the CSP blocks TikTok's player script** `blog`
  - **Page:** `/blog/{slug}` with a TikTok video URL
  - **Viewport:** both
  - **What to do:** In `prolyv/web/headers.py`, `script-src` allows www.tiktok.com but `embed.js` loads its code from `*.ttwstatic.com`. Allow that host, then re-test with a real video. Canvas: BlogPost, MBlogPost (video figure).
  - **Seen:** Only a plain "Watch on TikTok" link and 0 iframes. Console: loading `https://sf16-website-login.neutral.ttwstatic.com/obj/tiktok_web_login_static/tiktok/falcon/embed/embed_v1.0.14.js` violates script-src. This happens before the video ID is used, so a real ID will not help.
  - **Steps:** As karyna, `/admin/posts/new`, set Video URL to `https://www.tiktok.com/@prolyv/video/7123456789012345678`, tick Published, Create post. Open the post and read the console.

- [ ] **Editing a brew-along session silently removes it from the post** `blog`
  - **Page:** `/sessions/{id}/edit`, then `/blog/{slug}#brew-along`
  - **Viewport:** both
  - **What to do:** Fill `post_id` in the edit form from `sess.post_id` in `routes_diary`, or leave `sess.post_id` unchanged on edit.
  - **Seen:** The edit form renders `post_id` empty with no "Brewing along" notice. After saving, the heading goes from "Brewed along · 14" to "Brewed along · 13" and the session is gone, with no message.
  - **Steps:** As olena_tea, open a public session listed under `/blog/first-gongfu-session`. Open `/sessions/{id}/edit`, change the note, click Save changes. Reload the post.

- [ ] **The post page has no Share button** `blog`
  - **Page:** `/blog/{slug}`
  - **Viewport:** both
  - **What to do:** Add `<button data-share="{{ og.url }}">` beside Brew along on desktop and in the top bar on phone. The `app.js` handler already exists. Canvas: BlogPost, MBlogPost.
  - **Seen:** Only Brew along, Save, Tea card (when linked) and, for admins, Edit post. The rendered post contains no `data-share`.
  - **Steps:** Open any post, signed in and signed out, on desktop and phone, and list the hero controls.

- [ ] **The blog index departs from the approved design** `blog`
  - **Page:** `/blog`
  - **Viewport:** both
  - **What to do:** Rebuild `templates/blog/index.html` and the `post_card` macro from Blog.dc.html and MBlog.dc.html: glowing hero with "THE HOME OF OUR SHORT VIDEOS", H1 "Blog", the "Every video we post…" copy and TikTok and Instagram pills; large LATEST card; "All posts" with category tabs (All, Video breakdowns, Guides, Stats); cards with Cup cover, badge, category, read time, date and comment count; aside with "New breakdowns by email", "Series" and "Most brewed along".
  - **Seen:** Flat header with "BLOG · N POSTS", H1 "Full brewing breakdowns", then a plain 2-column list showing only "Blog · date", title and summary. A seeded post has no cover, cup or picture. None of the other blocks exist. No TikTok or Instagram link on the blog in this build.
  - **Steps:** Open `/blog` at 1440 and 375 and compare with Blog.dc.html and MBlog.dc.html.

- [ ] **The post page is a plain article, not the approved layout** `blog`
  - **Page:** `/blog/{slug}`
  - **Viewport:** both
  - **What to do:** Add structured steep data to Post and rebuild `templates/blog/post.html` from BlogPost.dc.html and MBlogPost.dc.html: two-column hero with a tall video figure, breadcrumbs, category and read-time eyebrow, author with @account; "The session from the video" card with "Copy to my diary"; interactive "Steep by step" with a sticky cup; brew-along block with "Add my session", compact rows led by the person's name, and an average, common-note and common-change line; aside with "In this series", a forum thread and "Start your own diary"; phone top bar with Back and Share.
  - **Seen:** A single narrow column with eyebrow "Blog · date · tea" and a small video block below the hero. None of the blocks above exist. Brew-along has no "Add my session", even in the empty state, and rows are full session cards titled with the tea name.
  - **Steps:** Open `/blog/first-gongfu-session` at 1440 and 375 and compare with the canvas.

### Profiles

- [ ] **The @prolyv brand account cannot save its profile** `people` `security`
  - **Page:** `/settings` as @prolyv
  - **Viewport:** both
  - **What to do:** In `prolyv/web/routes_account.py` `settings_save`, skip the reserved and validity checks when `handle == user.handle`. `handle_check` already handles "yours" first.
  - **Seen:** HTTP 400 "That handle is reserved." although the live check says "That's your current handle". Name and bio of the owner's public account cannot be edited. Any account holding a reserved handle has the same problem.
  - **Steps:** Sign in as prolyv, open `/settings`, change the bio or display name, leave the handle, press Save profile. Reproduced on 8100 and a private copy.

- [ ] **Desktop profile is missing the tabs, the sidebar and two whole sections of the design** `people`
  - **Page:** `/u/{handle}`
  - **Viewport:** desktop
  - **What to do:** Build the tab row (Sessions, Topics & replies, Shelf, Tea profile); aside with Badges, Most helpful reply and the "Your diary could look like this / Start for free" CTA; Shelf grid; Tea profile with share bar and TOP NOTES; heading "Public sessions" with a logged/shared count line. Canvas: Profile.dc.html.
  - **Seen:** One column: "Sessions" then "Topics". The user's replies are not listed anywhere. Stat labels are "public sessions / helpful replies / followers / following" instead of "sessions logged / helpful replies / followers / teas on the shelf".
  - **Steps:** Open `/u/olena_tea` at 1440 and compare with Profile.dc.html.

- [ ] **Phone profile does not follow MProfile** `people`
  - **Page:** `/u/{handle}`
  - **Viewport:** phone
  - **What to do:** Add own top bar (back arrow, @handle, More); order as cup, eyebrow, name, bio, stats, full-width Follow above Ask; section tabs, then Badges and Most helpful reply. Canvas: MProfile.dc.html.
  - **Seen:** Global Prolyv header. Actions come before stats and sit side by side. No tabs, badges, most helpful reply, Shelf or Tea profile.
  - **Steps:** Open `/u/olena_tea` at 375x812 and compare with MProfile.dc.html.

- [ ] **Profile hero shows a flat lilac disc instead of the animated tea cup** `people`
  - **Page:** `/u/{handle}`
  - **Viewport:** both
  - **What to do:** Use the spinning Cup (220px desktop, 150px phone) with initials overlaid. Canvas: Profile.dc.html, MProfile.dc.html.
  - **Seen:** Static gradient circle with initials (`.avatar-initials`), no cup or animation.
  - **Steps:** Open a profile without an avatar photo, for example `/u/olena_tea`, on desktop and phone.

- [ ] **Profile silently stops at 20 sessions, so older public sessions cannot be reached** `people`
  - **Page:** `/u/{handle}`
  - **Viewport:** both
  - **What to do:** `public_sessions_for_user` has `limit=20`. Add paging, a "show more" or a link to the full list.
  - **Seen:** The stat says 22, but exactly 20 cards render with no more/next control.
  - **Steps:** Log 22 public sessions as one user and open their profile.

- [ ] **"Ask Olena in the forum" opens a blank new-topic form with no link to Olena** `people`
  - **Page:** `/u/{handle}`
  - **Viewport:** both
  - **What to do:** Make the question reach the person through a prefilled mention or notification, or send the button to a relevant topic as the canvas does (it links to Topic).
  - **Seen:** Goes to plain `/discussions/new` with no mention of Olena. Code that creates a topic only notifies the poster's followers. A topic was not posted through this button to confirm Olena gets nothing.
  - **Steps:** Signed in, open `/u/olena_tea` and press "Ask Olena in the forum".

### Tea base and search

- [ ] **Main photo on tea cards shows something that is not the tea** `tea-base`
  - **Page:** `/teas/tie-luo-han`, `/teas/liu-bao`, `/teas/sejak`, `/teas/ujeon`, `/teas/ruhuna`, `/teas/java-black`, `/teas/thai-black`, `/teas/russian-caravan`, `/teas/zhu-ye-qing`, `/teas/hyson-young-hyson`, `/teas/kangra-black`, `/teas/kangra-green`, `/teas/charleston-tea`
  - **Viewport:** both
  - **What to do:** Clear these primary images so the cup placeholder shows, or re-pick them. The importer evidently matched by keyword with no review. The list is what could be confirmed at thumbnail size, so it may not be complete.
  - **Seen:** Tie Luo Han: soup dumplings (`Xiao_long_bao_closeup_in_Shanghai.jpg`). Liu Bao: Taiwanese presidential ceremony (馬英九總統). Sejak and Ujeon: Korean food platter (`Gujeolpan.jpg`). Ruhuna: handwritten 1859 diary page. Java Black: cacao nursery. Thai Black: tuna salad. Russian Caravan: the Russian Tea Room front. Zhu Ye Qing: a shop in a mall. Young Hyson: a trade token. Kangra Black and Green: a road through trees. Charleston Tea: a signpost. The dumplings and the politician also appear as thumbnails on `/teas`.
  - **Steps:** Open each tea page, or find it in the `/teas` grid.

- [ ] **Gallery photos include coffee machines, chicken tikka, a grasshopper, dumplings and a map** `tea-base`
  - **Page:** `/teas/{slug}` gallery thumbnails
  - **Viewport:** both
  - **What to do:** Review the gallery by hand, or show the primary image only until it has been curated.
  - **Seen:** Mi Xiang Black: two Nescafe Dolce Gusto machines. Kashmiri Kahwa: `Chicken_Tikka.jpg`. Peppermint: a grasshopper nymph. Xiao_long_bao on Bai Ji Guan, Jin Xuan Milk Oolong, Shui Jin Gui and Wuyi Qi Lan. Kandy: a pelican and `Trust_is_the_epitome_of_peace`. Masala Chai: "A Kolkata voter". Miang: `Meal_in_Turkmenistan`. Hojicha: Aburi-mochi. Wuyi Fo Shou and Yongchun Fo Shou: `Taiwanese_cuisine`. Nilgiri Black: a map of Kongu Nadu. Kenyan Purple Tea: a purple cartoon ghost. Hojicha Powder: a hanko logo. Thai Black: bingsu and salad. Bai Mu Dan (a white tea): `Great_Earl_Grey_Damann`. Ruhuna: two more diary pages.
  - **Steps:** Open `/teas/mi-xiang-black`, `/teas/kashmiri-kahwa`, `/teas/peppermint`, `/teas/bai-ji-guan`, `/teas/kandy`, `/teas/nilgiri-black`, `/teas/kenyan-purple-tea`, `/teas/thai-black` and click the thumbnails.

- [ ] **Region list is unparsed prose: broken labels, whole sentences, 22,000 px on a phone, one-tea URLs in the sitemap** `tea-base` `admin-static`
  - **Page:** `/teas/origins`, `/teas/o/{key}`, `/sitemap.xml`
  - **Viewport:** both
  - **What to do:** Use short, clean region names, each listed once (the canvas uses "Аньсі, Фуцзянь"). The route splits `origin_region` on the middle dot only, and the importer builds `origin_keys` from the text. Key only on the leading place name or curated regions, add a clean short region field, list only regions with 3 or more teas, and keep the prose as a note on the card.
  - **Seen:** 566 chips: 157 longer than 35 characters, 124 whole sentences by another tester's count, and 9 with unbalanced brackets ("Hengzhou)", "Fuding and Fuzhou (also Guangxi", "Lingyun (also Hezhou", "Boseong and Jeju)"). Most have count 1. The page is 22,315 px tall on a phone. `/teas/o/hengzhou` has H1 "Teas from Hengzhou)". The Assam chip is a full sentence whose text spills out of the pill, its H1 is that sentence, and slugs run to 191 characters. The long-key page has a 13-line H1 for a single tea. Others: "chai stalls and home kitchens", "Pan-India". Fuding, "Fuding and Zhenghe" and "Fuding, Zhenghe and Jianyang" are separate regions. 124 of these URLs are thin one-tea pages in the sitemap.
  - **Steps:** Open `/teas/origins` and read the China and India cards. Click "Hengzhou)". Open `/teas/style-ctc-vs-orthodox` and follow its second origin link. Open `/teas/o/yunnan-pu-erh-fujian-aged-white-tea-and-wuyi-oolong-guangdong-aged-dancong-hunan-and-guangxi-dark-teas-taiwan-aged-oolong-stored-in-kunming-guangzhou-hong-kong-taiwan-and-malaysia`.

- [ ] **Long origin sentences spill into tea cards and the tea page eyebrow** `tea-base`
  - **Page:** `/teas` (cards), `/teas/{slug}` eyebrow and Origin fact, `/search` tea rows
  - **Viewport:** both
  - **What to do:** Clamp `.tea-card-origin` to 2 lines. In the eyebrow show country plus first region only, and keep the full sentence in the Origin fact. Canvas: TeaPage hero chips.
  - **Seen:** Card origin runs up to 200+ characters. The Aged Teas card is 516px tall on a phone against about 371px normally, and Peppermint's origin wraps to 9 lines. The Earl Grey eyebrow on a phone is 5 uppercase lines. The longest eyebrow is 220 characters on `/teas/style-aged-teas`, each segment linking to a one-tea origin page.
  - **Steps:** On a phone open `/teas?q=aged`. On `/teas` page 1 look at Earl Grey and Peppermint. Open `/teas/earl-grey` and `/teas/style-aged-teas`.

- [ ] **Origin dropdown does nothing on origin pages** `tea-base`
  - **Page:** `/teas/o/{key}`
  - **Viewport:** both
  - **What to do:** The path parameter wins over the query parameter. Remove the select on `/teas/o` pages or redirect to the chosen origin.
  - **Seen:** The select says Japan and the URL becomes `/teas/o/china?q=&origin=japan&form=&sort=popular`, but results stay Chinese under "Teas from China". On `/teas/o/fujian` the select reads "Any origin" while limited to 58 Fujian teas, and choosing it does not clear the limit.
  - **Steps:** Open `/teas/o/china` and choose Japan in the origin select. Then open `/teas/o/fujian`.

- [ ] **Search for "puerh" or "puer" finds nothing** `tea-base`
  - **Page:** `/teas`, `/search`
  - **Viewport:** both
  - **What to do:** Add puerh and puer as aliases and search terms for pu-erh teas, or index a form of each name with spaces and hyphens removed.
  - **Seen:** "puerh" and "puer" give 0 on both screens. "pu-erh" and "pu erh" give 57 on `/teas`, "pu'er" gives 14. "dahongpao" gives 1 against 4 for "da hong pao".
  - **Steps:** Search those strings in the `/teas` box and on `/search`.

- [ ] **Russian search is promised but returns nothing, and Russian text sits in the English-only UI** `tea-base` `admin-static`
  - **Page:** `/teas`, `/teas/o/{key}`, `/search`, `/admin/teas/new`
  - **Viewport:** both
  - **What to do:** Drop "по-русски" and "Russian name" from placeholders and empty states until `name_ru` is filled. Remove the admin "Russian name" field and its Cyrillic placeholder, or confirm with the owner that they are wanted.
  - **Seen:** улун, пуэр, пу-эр, пуер, те гуань инь and Да Хун Пао all return 0. Only names with Cyrillic in the native-name field match, such as Ромашка. 0 of 441 teas show a Russian name. The empty state tells the user to try exactly what just failed. The admin form has placeholder "Те Гуань Инь".
  - **Steps:** Search those terms on `/teas` and `/search`. Open `/admin/teas/new`.

- [ ] **Search All tab silently drops discussions, blog posts and sessions on common words** `tea-base`
  - **Page:** `/search`
  - **Viewport:** both
  - **What to do:** Show a few results per type under headings on All, and add paging on the type tabs.
  - **Seen:** Results are hard-capped at 120 with no count, paging or notice. q=tea: All shows 119 teas and 1 post while the tabs hold 7 discussions, 6 blog posts and 1 session. q=black: 120 teas and nothing else while Blog has 2. The Teas tab also stops at 120. `/search?q=oolong` is 17,135px tall on a phone.
  - **Steps:** Open `/search?q=tea`, count results by type and open the other tabs. Repeat with q=black and q=green.

---

## Minor

### Shell, login and feed

- [ ] **Flash message box is misaligned on desktop** `shell-feed` `diary` `blog`
  - **Page:** Any page with a flash message
  - **Viewport:** desktop
  - **What to do:** `.flash-ok` and `.flash-error` set left and right margins to 16px, overriding the auto centring in `.flash`. Use `width:calc(100% - 32px)` with `margin-inline:auto`. It should align with the content column (x=92 to 1332).
  - **Seen:** Box at x=16 to 1256, stuck to the left.
  - **Steps:** At 1440, sign out, save a session or publish a post, then measure `.flash`.

- [ ] **Saved, Settings and Log out are hard to find** `shell-feed` `people`
  - **Page:** Header, tab bar, footer, `/u/{me}`, `/settings`
  - **Viewport:** both
  - **What to do:** Add a direct way to reach them, for example an avatar menu. Log out should be obvious from the shell. The canvas does not define this, so it is a product gap (Header.dc.html, MobileNav.dc.html).
  - **Seen:** The avatar is a plain link to the profile. Saved is linked only from the footer. Settings is reached only through "Edit profile" on your own profile. Log out is at the bottom of Settings, below "Delete account". The logout action itself works.
  - **Steps:** Signed in, look for these in the header, footer and tab bar. Follow avatar, Edit profile, and scroll Settings to the end.

- [ ] **The next destination is lost on three login paths** `shell-feed`
  - **Page:** `/login`
  - **Viewport:** both
  - **What to do:** Land the user on the page they asked for after any sign-in path, and keep `next` through validation errors. New accounts should get an onward link.
  - **Seen:** (a) Lands on `/`, because the button links to plain `/dev/login` (local only). (b) Redirects to `/login` with the error flash and no `next`, so the next attempt ends on `/`. (c) A new account always lands on `/settings` with no onward link, although the flash says "then start your diary". A returning e-mail user with `next=/sessions/new` correctly lands there.
  - **Steps:** (a) Open `/login?next=/diary`, click "Continue as demo user". (b) Open `/login?next=/sessions/new`, submit `foo@bar`. (c) Open `/login?next=/diary` with a new e-mail and finish the link flow.

- [ ] **Login page differs from the design in cup size and phone sheet** `shell-feed`
  - **Page:** `/login`
  - **Viewport:** both
  - **What to do:** Desktop: Cup at 380px. Phone: tagline 28px, "Log in to Prolyv" 22px, in a sheet with 28px rounded top corners overlapping the brand block by 24px. Canvas: Login, MLogin.
  - **Seen:** Desktop cup 220px. Phone tagline 26px, heading 28px, a straight edge with no sheet. Local-only notes replace Telegram and Google buttons here, pushing the e-mail button to y=1026 on the phone.
  - **Steps:** Open `/login` signed out at 1440 and 375 and measure.

- [ ] **Internal roadmap wording visible to users** `shell-feed` `people`
  - **Page:** `/settings` (Avatar card)
  - **Viewport:** both
  - **What to do:** Replace "Uploads arrive with the diary milestone." with user-facing copy. The diary is already live, and this is the first page a new e-mail user sees.
  - **Seen:** The sentence above, verbatim.
  - **Steps:** Sign in with a new e-mail account and read the Avatar section.

- [ ] **Topic cards in the feed use a smaller body-font title and drop the attached-session pill and last-reply info** `shell-feed`
  - **Page:** `/` topic cards
  - **Viewport:** both
  - **What to do:** Title in Unbounded 21px (17px on phone), a pill with a mini cup and "Attached session: params", "last reply 5 min ago" in the meta row, and per-section badge colours. Canvas: Feed, MFeed.
  - **Seen:** Title in Manrope 17px on both viewports (`.topic-title` has no font family), plain "session attached" text, no last-reply time, and all section badges in one colour.
  - **Steps:** Open `/?tab=discussions` and compare with the topic card in Feed.

- [ ] **Feed tabs wrap to a second row and differ from the segmented control** `shell-feed`
  - **Page:** `/`
  - **Viewport:** phone
  - **What to do:** Use one rounded segmented control in a single row, 40px tall, with the active state exposed (`aria-pressed` in the canvas). Canvas: MFeed.
  - **Seen:** Separate 36px chips. "Following" wraps alone to a second row. The active chip has no `aria-current` or `aria-pressed`.
  - **Steps:** Open `/` signed in at 375.

- [ ] **No follow or save action exists in the feed (needs owner decision)** `shell-feed`
  - **Page:** `/` feed cards
  - **Viewport:** both
  - **What to do:** The brief lists follow and save actions in the feed, but the canvas shows none on the cards. Get an owner decision, then add them or close this.
  - **Seen:** Zero forms and buttons inside the feed. Follow exists only on profiles and Save only on detail pages. Both work there.
  - **Steps:** Open `/` signed in and count forms and buttons inside `main`. Then test Follow on `/u/olena_tea` and Save on a session page.

### New session and diary

- [ ] **Saving with the timer running loses that steep and leaves a stale draft** `diary`
  - **Page:** `/sessions/new`
  - **Viewport:** both
  - **What to do:** Stop and record the running steep, or warn. Leave no draft after a successful save: clear `running` on submit, or skip the `beforeunload` save after submit.
  - **Seen:** The running steep is dropped. The `beforeunload` handler re-saves the draft after submit, so the next "Log a tea" opens "Draft restored" with the session already saved. Saving again would duplicate it.
  - **Steps:** Log a tea, type a name, press "Start steep", press "Save to diary" while it runs. Open Log a tea again.

- [ ] **Draft is wiped before the server accepts the save, so a failed save loses the entry** `diary`
  - **Page:** `/sessions/new`
  - **Viewport:** both
  - **What to do:** Remove the draft on the page after the redirect, not in the submit handler.
  - **Seen:** The "That form has expired" 403 appears and localStorage is already empty (draft = null).
  - **Steps:** Fill in the form. Make the CSRF check fail (for example, another sign-in in a second tab), then press Save.

- [ ] **An unsent diary draft carries over to the next account on the same browser** `diary` `security`
  - **Page:** `/sessions/new` (draft autosave)
  - **Viewport:** both
  - **What to do:** In `prolyv/static/session.js:10`, include the user id in `draftKey`, and clear `prolyv:session-draft:*` on logout (submit handler on the logout form).
  - **Seen:** The next account gets "Draft restored from this device" with the previous user's tea, note, rating and steeps, and could publish them under their own name. `prolyv:session-draft:new` stays in localStorage after logout.
  - **Steps:** As demo_user, open `/sessions/new`, type a tea and note, and do not save. Log out (or open `/dev/login/olena_tea`), sign in as another user, open `/sessions/new`.

- [ ] **Cancel keeps the draft** `diary`
  - **Page:** `/sessions/new`
  - **Viewport:** both
  - **What to do:** Make Cancel discard the entry, or ask first.
  - **Seen:** The form reopens with "Draft restored" and the cancelled tea name. Only the small "discard" link clears it, and that works.
  - **Steps:** Type a name, press Cancel, open Log a tea again.

- [ ] **Edit form restores unsaved drafts only for sessions without steeps** `diary`
  - **Page:** `/sessions/{id}/edit`
  - **Viewport:** both
  - **What to do:** Add `data-editing` to the form in edit mode (the JS intends no restore on edit but the template never sets it), or implement edit drafts deliberately.
  - **Seen:** On a 0-steep session the note comes back as "Unsaved edit" with "Draft restored". Sessions with steeps never restore.
  - **Steps:** Edit a 0-steep session, change the note, leave without saving, reopen edit. Repeat on a session with steeps.

- [ ] **Picking a different tea keeps the first tea's parameters and steep suggestions** `diary`
  - **Page:** `/sessions/new` (tea picker)
  - **Viewport:** both
  - **What to do:** Make parameters and suggested seconds follow the newly linked tea, at least when the user has not edited them.
  - **Seen:** Da Hong Pao linked with 5 g, 150 ml, 75 °C and "suggested 60 s", all from Sencha.
  - **Steps:** Type "sencha" and pick Sencha (5 g, 150 ml, 75 °C, 60 s). Clear the name, type "shu" and pick the first result, Da Hong Pao (card: 8 g, 110 ml, 100 °C, 10 s).

- [ ] **Suggestion list stays open when clicking elsewhere and covers the parameter fields** `diary`
  - **Page:** `/sessions/new` (tea picker)
  - **Viewport:** both
  - **What to do:** Close the list on an outside click.
  - **Seen:** All 8 suggestions stay open over Leaf, Water and Temp until one is picked.
  - **Steps:** Type "sencha" and click on an empty page area.

- [ ] **"Add by hand" adds a different value than typed for invalid input** `diary`
  - **Page:** `/sessions/new`
  - **Viewport:** both
  - **What to do:** Show an error or add nothing, and give feedback when the box is empty.
  - **Seen:** A steep with the suggested value (45 s) is added and -5 stays in the box. With an empty box and no suggestion the button silently does nothing.
  - **Steps:** On a tea with suggestions, type -5 and press "Add by hand".

- [ ] **Non-finite numbers and file parts in form fields give a 500 instead of a validation message** `diary` `security`
  - **Page:** POST `/sessions/new`, `/sessions/{id}/edit`, any form
  - **Viewport:** both
  - **What to do:** In `routes_diary.py` `parse_session_form`, catch OverflowError with ValueError in `number()` and the steeps loop, and treat non-string values as empty. In `deps.py` `require_csrf`, check `isinstance(token, str)`. Return a 400 with a field error, as `grams=inf` and `water_ml=nan` already do.
  - **Seen:** 500 "Something went wrong", and the entered session is lost. Log: `OverflowError: cannot convert float infinity to integer`; `AttributeError: 'UploadFile' object has no attribute 'strip'`; a TypeError from `hmac.compare_digest`. Nothing leaks. Testers differ on reach: one says Water is a plain text input so a user can type `inf`, another treats this as crafted input only. Please check the field type.
  - **Steps:** Signed in, POST `/sessions/new` with `water_ml=inf`, `temp_c=1e999`, `rating=inf`, `steep_seconds=inf` or `steep_seconds=1e400`. Separately, send a multipart form with `tea_name` or `csrf_token` as a file part.

- [ ] **An out-of-range steep disappears on the error page** `diary`
  - **Page:** `/sessions/new` (POST with an error)
  - **Viewport:** both
  - **What to do:** Keep all steeps in the list so the user can fix the bad one.
  - **Seen:** "Steep times must be between 1 second and an hour." shows, but the list holds 1 item. The offending steep and its note are gone.
  - **Steps:** Submit steeps 9999 and 30 with another field invalid.

- [ ] **Number, date and file inputs are unstyled native controls** `diary`
  - **Page:** `/sessions/new`, `/sessions/{id}/edit`
  - **Viewport:** both
  - **What to do:** Apply the same dark rounded field style as other inputs (rgba white 6% background, 14px radius).
  - **Seen:** Square grey boxes (rgb(59,59,59), radius 0), a monospace-looking date field and a default "Choose files" button.
  - **Steps:** Look at the "seconds" box, steep rows, Date and "Choose files".

- [ ] **Tomorrow's date is accepted** `diary`
  - **Page:** `/sessions/new`
  - **Viewport:** both
  - **What to do:** Apply "That date is in the future." to today + 1 (it already fires for +2), and set `max` on the date input.
  - **Seen:** Saved. It sits at the top of the diary above "Today" under "Friday, 6 November" and fills a calendar cell.
  - **Steps:** Save a session dated today + 1 (2026-11-06 at test time).

- [ ] **Stat tiles mix time ranges with no label** `diary`
  - **Page:** `/diary`
  - **Viewport:** both
  - **What to do:** Make all stats share the 30-day window the hero promises, or label each one.
  - **Seen:** "12 last 30 days" sits next to "average rating", "steeps" (36) and "most brewed", which count all-time sessions.
  - **Steps:** Add a session dated January and compare the tiles.

- [ ] **Story text defects: words join across lines, long notes vanish, unbroken text runs off the card** `diary`
  - **Page:** `/sessions/{id}/story.png`
  - **Viewport:** n/a
  - **What to do:** Normalise `\r\n` before splitting, replace newlines with a space in `_clean`, and hard-wrap long words in `_wrap`. Show only the first paragraph.
  - **Seen:** "line oneline two" and "Para one.Para two". Browser notes use CRLF, so the split on `\n\n` never matches. With a long title and 13 steeps the note is dropped and the steeps line stops at 10 with no ellipsis. Unbroken name and note run off the right edge.
  - **Steps:** Make a note with a line break. Make a browser-saved note with two paragraphs. Use a 3-line title with many steeps. Use an unbroken long name. Open `story.png` for each.

- [ ] **Line breaks in comments are collapsed and pasted URLs are not clickable** `diary` `blog`
  - **Page:** `/sessions/{id}` comments, `/blog/{slug}` comments
  - **Viewport:** both
  - **What to do:** Add `white-space: pre-line` to `.comment p`, or use the forum's user-text rendering. Link URLs (see the forum URL item).
  - **Seen:** "line one line two line four" on one line (`white-space: normal`). Blog comments behave the same.
  - **Steps:** Post a multi-line comment with a blank line, then view it.

### Forum

- [ ] **Topic cards lack the avatar, views and last-reply details of the design, and dates are not relative** `forum`
  - **Page:** `/discussions`, `/discussions/{id}` reply heads
  - **Viewport:** both
  - **What to do:** Add initials avatar, section tag, PINNED, SOLVED and SESSION badges, relative time ("2 h ago"), reply and view counts and "last reply ...". Strip quote markup from excerpts. Canvas: Forum, MForum, Topic.
  - **Seen:** No avatar, views or last-reply author. SESSION is trailing text "session attached". Bare dates, so every reply in a same-day thread shows the same stamp. A non-design excerpt shows raw `> quoted para` markup.
  - **Steps:** Compare a topic card with the Forum canvas, and reply heads in a same-day thread with the Topic canvas.

- [ ] **Quoting a reply that starts with a quote loses the reply's actual text** `forum`
  - **Page:** `/discussions/{id}`
  - **Viewport:** both
  - **What to do:** `static/app.js` takes only the first paragraph (split on blank line). Skip leading paragraphs starting with `>`.
  - **Seen:** The box receives `> @karyna: > @prolyv: earlier point` followed by blank lines. Her real answer is gone and a stray nested `>` renders literally.
  - **Steps:** As karyna, reply `> @prolyv: earlier point`, a blank line, then "My real answer is in this second paragraph...". As another user, click quote on it.

- [ ] **Reply delete has no confirmation and throws you back to the top of the thread** `forum`
  - **Page:** `/discussions/{id}`
  - **Viewport:** both
  - **What to do:** Add `data-confirm` to the reply delete form and a `set_flash`, and return the user near where they were.
  - **Seen:** Deleted at once with no dialog (21 replies became 20), the page reloads at scrollY 0, and no flash. The button sits right next to "quote".
  - **Steps:** As a reply's author, click "delete" under it.

- [ ] **After Helpful or Mark as helpful, the target reply lands under the sticky header** `forum`
  - **Page:** `/discussions/{id}#reply-{id}`
  - **Viewport:** desktop
  - **What to do:** Add `scroll-margin-top` or `scroll-padding`; none exists in `app.css`. This anchor is also the redirect target after posting a reply and is used by notification links.
  - **Seen:** Reply top at y=1 while the header ends at y=73, hiding the author, date and first line.
  - **Steps:** At 1440x900 in a long thread, click Helpful on a reply well down the page.

- [ ] **Moderator-hidden replies still count in lists and still match search** `forum`
  - **Page:** `/discussions/{id}`, `/discussions?q=`
  - **Viewport:** both
  - **What to do:** Exclude hidden replies from `reply_count` and from search results.
  - **Seen:** The topic page says "0 replies" but the list card says "1 reply". Searching "zanzibar" returns the topic.
  - **Steps:** As karyna, hide the only reply on a topic. Signed out, open the topic and its section list, and search a unique word from the hidden reply ("zanzibar").

- [ ] **Replies are accepted on a hidden topic** `forum`
  - **Page:** POST `/discussions/{id}/replies`
  - **Viewport:** n/a
  - **What to do:** Reject with a 404 or a clear message.
  - **Seen:** 303 to the reply anchor and the reply is stored. The redirect lands on a 404 for that user, and the reply appears when the topic is unhidden. By code the owner also gets a notification pointing at a page they cannot open (not checked).
  - **Steps:** As karyna, hide a topic. As demo_user, POST a reply with a valid CSRF token from an already open tab.

- [ ] **The post-limit page has no way back to the thread, loses comment text, and also blocks Report** `forum` `blog`
  - **Page:** 429 page after Reply, New topic, Report or comment
  - **Viewport:** both
  - **What to do:** Add a link back to the thread or post, and do not block a safety action like Report with the chat quota.
  - **Seen:** "Slow down a little..." with links "Back to login" and "Home" for a signed-in user, on the POST URL. Browser Back restored text in a forum reply in Chromium, but nothing points to that; on a blog comment the typed text was reported gone. Send report also returned this 429 for olena_tea, because reports share the post quota, so the report is not stored.
  - **Steps:** Post replies quickly until refused, then click Reply, or Send report on a reply. Also hit the comment limit on a blog post.

- [ ] **Action labels and hierarchy differ from the canvas** `forum`
  - **Page:** `/discussions/{id}`
  - **Viewport:** both
  - **What to do:** Use sentence-case "Quote" and "Reply" as 14px, weight 800 actions; submit button "Post reply"; an "Attach a session" button; Share on the opening post; centre Report with the Save pill; add space around the H1. Canvas: Topic, MTopic.
  - **Seen:** Lowercase underlined pink "quote" and "delete", larger than the small grey "Report". Submit is "Reply". Attach is a native select "Attach a public session". Report sits at the top of the row. The H1 is tight against the Solved badge and author chip.
  - **Steps:** Compare reply actions and the reply form with the Topic canvas.

- [ ] **URLs in posts are not clickable** `forum`
  - **Page:** `/discussions/{id}`
  - **Viewport:** both
  - **What to do:** Make pasted links clickable, which matters for the design's house rule "say where a tea is from if you recommend it". Canvas: Topic.
  - **Seen:** Plain, unlinked text. Markdown markers show literally.
  - **Steps:** Post a topic body containing `https://example.com` and `**bold**`, then view it.

- [ ] **Attached session card squeezes its text into a narrow column** `forum`
  - **Page:** `/discussions/{id}` (attached session)
  - **Viewport:** desktop
  - **What to do:** Let the text use the available card width. Canvas: Topic.
  - **Seen:** The card is about 670px wide but name and note wrap at about 260px, with a large empty area left of the stars. A long tea name takes 7 lines.
  - **Steps:** Attach a session with a long tea name or note, and view the topic at 1440.

- [ ] **Helpful looks like a button but does nothing when signed out or on your own reply** `forum`
  - **Page:** `/discussions/{id}`
  - **Viewport:** both
  - **What to do:** Signed out: link to login, as Save does (`/login?next=`). Own reply: make it visibly static. Canvas: Topic.
  - **Seen:** The pill looks identical to the active button and is inert, with no prompt.
  - **Steps:** Signed out, or as a reply's author, tap the Helpful pill.

### Blog and brew-along

- [ ] **The comment form and list differ from the design** `blog`
  - **Page:** `/blog/{slug}` comments
  - **Viewport:** both
  - **What to do:** At minimum add a visible "Join the discussion" label. Then add "Attach my session", a gradient "Post" button, relative times, a nested "Prolyv · author" reply and "Show N more comments". Canvas: BlogPost, MBlogPost.
  - **Seen:** Placeholder only, a small secondary "Comment" button, absolute dates, no replies or author badge, and all 19 comments render at once.
  - **Steps:** Open a post signed in and compare. Also open `/blog/keeping-a-tea-diary`.

- [ ] **The comment validation error appears off-screen** `blog`
  - **Page:** `/blog/{slug}#comments`
  - **Viewport:** both
  - **What to do:** Show the error inline in the form, or make the flash sticky, and keep the typed text.
  - **Seen:** The browser jumps to `#comments` while "Write a comment first." is a static banner at the top. On the phone scrollY was 2300 and the banner's top was -2219. The form comes back empty.
  - **Steps:** Signed in, type one character in the comment box and click Comment.

- [ ] **A hidden session leaves a contradictory heading and empty state** `blog`
  - **Page:** `/blog/{slug}#brew-along`
  - **Viewport:** both
  - **What to do:** In `routes_blog.py`, add `TeaSession.hidden.is_(False)` to the `brew_along_count` query.
  - **Seen:** "Brewed along · 1" directly above "Nobody has brewed along yet".
  - **Steps:** Publish a post with one public brew-along session. As admin, hide it via `/admin/hide`. Open the post signed out.

- [ ] **Brew-along list stops at 12 with no way to see the rest** `blog`
  - **Page:** `/blog/{slug}#brew-along`
  - **Viewport:** both
  - **What to do:** Add "Show all" or pagination. Canvas: BlogPost (brew-along).
  - **Seen:** The heading counts all sessions but 12 cards render, followed directly by Comments.
  - **Steps:** Attach 14 public sessions to a post and open it.

- [ ] **Common TikTok and Instagram link forms do not embed, and no warning is shown** `blog`
  - **Page:** `/admin/posts/new`
  - **Viewport:** n/a
  - **What to do:** Widen the regex in `video_embed`, and warn on TikTok short links, which need resolving. The field label promises "TikTok or Instagram post/reel → embedded".
  - **Seen:** All four save without warning and render only a "Watch the video" button. `/p/`, `/reel/` and `/reels/` links on instagram.com do embed.
  - **Steps:** Create published posts with `https://vm.tiktok.com/ZMabc123/`, `https://www.tiktok.com/t/ZTabc/`, `https://m.tiktok.com/v/7123456789012345678.html` and `https://www.instagram.com/prolyv/reel/CxAbCdEfGhI/`.

- [ ] **http:// images are accepted and then blocked by the CSP** `blog`
  - **Page:** `/admin/posts/new`, `/blog`
  - **Viewport:** both
  - **What to do:** `read_post_form` accepts `http://`. Require `https://`, as the form's own error text says.
  - **Seen:** It saves. The console shows "violates img-src". `/blog` shows a broken-image card and the post cover collapses to height 0.
  - **Steps:** Create a post with cover `http://placehold.co/600x300.png` and Markdown `![x](http://placehold.co/300x100.png)`. Open the post and `/blog`.

- [ ] **A mistyped tea slug on a post is silently dropped** `blog` `admin-static`
  - **Page:** `/admin/posts/new`
  - **Viewport:** both
  - **What to do:** Add a field error such as "No published tea with this slug" in `read_post_form` or `apply_post_form`, or use the tea autocomplete here.
  - **Seen:** The flash says saved or "Published." The field comes back empty on edit, and the post has no tea link or brew-along prefill.
  - **Steps:** Create a post with Tea slug `no-such-tea`, tick Published, save, then reopen Edit.

- [ ] **Markdown table column alignment is dropped** `blog`
  - **Page:** `/blog/{slug}`
  - **Viewport:** both
  - **What to do:** Allow `text-align` through nh3 `filter_style_properties`, or map it to a class.
  - **Seen:** `<th>` and `<td>` have no alignment, because markdown-it emits style and nh3 strips it.
  - **Steps:** Publish a table with `|---|:--:|--:|` as the separator row and inspect the HTML.

- [ ] **A signed-out brew-along link loses the post** `blog`
  - **Page:** `/sessions/new?post={slug}`
  - **Viewport:** both
  - **What to do:** `login_redirect` should use the path plus the query string.
  - **Seen:** 303 to `/login?next=/sessions/new`, so `?post=` is lost and the session will not attach. The Brew along button itself keeps it.
  - **Steps:** Signed out, open `/sessions/new?post=first-gongfu-session` directly.

- [ ] **A brew-along session page has no link back to its post** `blog`
  - **Page:** `/sessions/{id}`
  - **Viewport:** both
  - **What to do:** Render a link such as "Brewed along with <post>" when `sess.post_id` is set.
  - **Seen:** The page HTML contains no `/blog/` link to the post.
  - **Steps:** Save a public session from Brew along, which lands on the session page. Look for the post.

### Profiles, notifications and saved

- [ ] **Notification avatars are squashed into ovals on the phone** `people`
  - **Page:** `/notifications`
  - **Viewport:** phone
  - **What to do:** Add `flex:none` on `.notifications .avatar`.
  - **Seen:** 24x36 and 18x36 on rows with wrapped text. Only a short row kept 36x36.
  - **Steps:** Open `/notifications` at 375 with notifications whose text wraps.

- [ ] **A failed delete or unlink shows its error off screen, so the button seems to do nothing** `people`
  - **Page:** `/settings`
  - **Viewport:** both
  - **What to do:** Redirect with a flash, or add an anchor to the failing section.
  - **Seen:** The page re-renders at the top showing the Profile card. The error is far below, the delete input is cleared, and the URL stays on `/settings/delete` or `/settings/unlink`.
  - **Steps:** In Delete account type a wrong handle and press Delete my account, or press Unlink on your only sign-in method.

- [ ] **Repeated follow toggles send duplicate notifications, and session creation and follows have no quota** `people` `security`
  - **Page:** `/u/{handle}/follow`, `/sessions/new`, `/notifications`
  - **Viewport:** n/a
  - **What to do:** Notify only on the first follow of a pair. Add `Depends(post_quota)` to `follow_toggle`, `session_create` and `session_edit`.
  - **Seen:** Three toggles on 8100 gave three identical "@tea_mike started following you" rows and a bell count of 3. On a private copy, 60 of 60 toggles passed and olena_tea received 33 notifications from one person. 40 of 40 sessions were created in 0.2 s. Followers also get a notification per public session.
  - **Steps:** As tea_mike, press Follow, Following, Follow, Following, Follow on Olena's profile, then open `/notifications` as olena_tea. For the quota: POST `/u/olena_tea/follow` 60 times and `/sessions/new` 40 times.

- [ ] **Old notifications 404 after a handle change or account deletion, and the old handle stays in the text** `people`
  - **Page:** `/notifications`, `/settings`
  - **Viewport:** n/a
  - **What to do:** Build the url and text from the actor at render time. Do not show the old handle of a deleted account. Warn in the handle field that old profile links stop working, or redirect old handles.
  - **Seen:** `/u/tea_mike` and `/u/demo_user` return 404 from the notification, and the text still shows the handles, though deletion is meant to erase the person. Settings gives no warning.
  - **Steps:** tea_mike follows Olena, then changes handle to `mike_renamed`. As Olena, open the notification. Repeat with an account that is then deleted.

- [ ] **The public sessions stat includes admin-hidden sessions** `people`
  - **Page:** `/u/{handle}`
  - **Viewport:** both
  - **What to do:** Filter `public_session_count` on `TeaSession.hidden`.
  - **Seen:** Stat 3, with 2 cards shown.
  - **Steps:** As admin, hide one of Olena's public sessions, then open `/u/olena_tea`.

- [ ] **The Sessions intro line reads like an empty-state placeholder above real sessions** `people`
  - **Page:** `/u/{handle}`
  - **Viewport:** both
  - **What to do:** Use "Public sessions" with a count line, as in the canvas: "214 logged · 96 shared. Private sessions stay private." Canvas: Profile.dc.html.
  - **Seen:** Heading "Sessions" and "Private sessions stay private. Public ones will appear here." above a filled list.
  - **Steps:** Open a profile that has sessions.

- [ ] **Following after the session expires ends on a "Wrong kind of request" page** `people`
  - **Page:** `/u/{handle}/follow`
  - **Viewport:** n/a
  - **What to do:** For POST requests without a session, use the Referer or the parent page as `next`.
  - **Seen:** The redirect target is the POST-only URL, and a signed-in GET on it returns 405. Both halves were tested with curl, not a full login.
  - **Steps:** Open a profile signed in, let the session end, press Follow. The POST redirects to `/login?next=/u/olena_tea/follow`. After sign-in the browser GETs that URL.

- [ ] **Saved items cannot be removed from the Saved page** `people`
  - **Page:** `/saved`
  - **Viewport:** both
  - **What to do:** Add a Remove or "Saved ✓" control on each card.
  - **Seen:** Zero buttons or forms in the main area. The user must open each item and toggle it there.
  - **Steps:** Save a tea, a session, a topic and a post, then open `/saved`.

- [ ] **Follower and following counts lead nowhere, so a user cannot list who they follow** `people`
  - **Page:** `/u/{handle}`
  - **Viewport:** both
  - **What to do:** Make the numbers open a list, or provide somewhere to see and manage who you follow.
  - **Seen:** The stats are plain text. The only list found is the handle list inside the JSON export.
  - **Steps:** Open any profile and try the "followers" and "following" stats.

- [ ] **Notification times are in UTC with no label** `people`
  - **Page:** `/notifications`
  - **Viewport:** both
  - **What to do:** Show the viewer's local time, or a relative time like the canvas ("1 h ago").
  - **Seen:** "5 Nov, 03:59" for an event at 05:59 EET, with no timezone.
  - **Steps:** Trigger a notification and compare with local time.

### Tea base and search

- [ ] **Live filter leaves the count and heading stale** `tea-base`
  - **Page:** `/teas`, `/teas/c/{cat}`
  - **Viewport:** both
  - **What to do:** htmx swaps only `#tea-results`. Move the header into the swap target or add an out-of-band swap.
  - **Seen:** Three cards (four for Japan plus Powder), but the eyebrow says "TEA BASE · 441 TEAS" and the H1 stays "Teas". A full reload shows "Tea base · 3 teas". Dropdown and chip counts never react: "Powder (5)" while 4 show under Japan.
  - **Steps:** On `/teas` type 铁观音, or choose Origin Japan and Form Powder.

- [ ] **Back button empties the search box but keeps filtered results** `tea-base`
  - **Page:** `/teas`
  - **Viewport:** both
  - **What to do:** Restore control values from the URL on `htmx:historyRestore`, or disable the history snapshot for this form.
  - **Seen:** The URL is `?q=matcha` and results are matcha, but the box is empty. Back from Sort A→Z leaves the select on "Most known first". Typing again starts from an empty query.
  - **Steps:** On `/teas` type "matcha", wait, type more so the query changes, then press browser Back.

- [ ] **Category chips drop the active filters and show global counts** `tea-base`
  - **Page:** `/teas`, `/teas/o/{key}`, `/teas/c/{cat}`
  - **Viewport:** both
  - **What to do:** Keep origin, form and search in chip links, and show counts that match the context.
  - **Seen:** The chip href is plain `/teas/c/green`, so the Japan filter is lost and you land on 94 teas, although `/teas/c/green?origin=japan` has 32. On `/teas/o/china` chips show global counts, "All" goes to `/teas`, and every chip leaves China.
  - **Steps:** Open `/teas?origin=japan` and click "Green 94". Open `/teas/o/china` and look at the chips.

- [ ] **Korea and South Korea are two separate countries** `tea-base`
  - **Page:** `/teas` origin select, `/teas/origins`
  - **Viewport:** both
  - **What to do:** Merge them into one country.
  - **Seen:** "South Korea (12)" and "Korea (3)", with separate pages.
  - **Steps:** Open the origin dropdown on `/teas`, or `/teas/o/korea` and `/teas/o/south-korea`.

- [ ] **Some photo credits are garbled or name no author** `tea-base`
  - **Page:** `/teas/{slug}` photo credit
  - **Viewport:** both
  - **What to do:** Clean the credits to "Photo: <author> · <licence> · source". Attribution is a licence condition.
  - **Seen:** "Photo: Unknown authorUnknown author · CC BY-SA 3.0". "Photo: Photo: VOA - H. Heuler". "Photo: No machine-readable author provided. Zantastik~commonswiki assumed (based on copyright claims). · CC BY-SA 3.0" on 4 teas. "Photo: http://persianfoodtours.com/". "Photo: Wikimedia Commons · CC BY-SA 3.0" on 9 teas.
  - **Steps:** Open `/teas/java-black`, `/teas/kenyan-ctc`, `/teas/ceylon-op-bop`, `/teas/lahijan-black`, `/teas/rou-gui` and read the caption.

- [ ] **Same main photo reused across different teas** `tea-base`
  - **Page:** `/teas/assam-ctc`, `/teas/darjeeling-second-flush`, `/teas/keemun-*`, `/teas/tie-guan-yin-*`, `/teas/dian-hong-*`
  - **Viewport:** both
  - **What to do:** Give different teas different photos, or show the placeholder.
  - **Seen:** 13 images are shared as main photos. Three Tie Guan Yin grades share one picture, four Keemun and four Dian Hong teas share one each, and so do Darjeeling First and Second Flush, Sencha and Shincha, Assam CTC and Assam Orthodox.
  - **Steps:** Compare main photos of related teas, for example search 铁观音 on `/teas`.

- [ ] **Originals used as thumbnails: about 5 MB on /teas page 1, thumbnails blank for seconds** `tea-base`
  - **Page:** `/teas`, `/teas/{slug}`
  - **Viewport:** both
  - **What to do:** Load small thumbnails for cards shown at about 165 to 300px, and add a placeholder while images load.
  - **Seen:** 22 measured images total about 5 MB, including one of 1,559 KB, with URLs marked `thumbnail_unscaled` and hotlinked from Wikimedia. On `/teas/da-hong-pao` all 4 gallery thumbnails were incomplete after 3 s and loaded by 6 s. Same blank slots on `/teas/earl-grey` on the phone.
  - **Steps:** Measure Content-Length of the images on `/teas`. Open `/teas/da-hong-pao` and read the thumbnail states after 3 s.

- [ ] **Tea page has no rating or session summary, and sessions and brewing sit far down** `tea-base`
  - **Page:** `/teas/{slug}`
  - **Viewport:** both
  - **What to do:** Add "N sessions · avg ★" and a breadcrumb to the hero, and on the phone move the Brewing card above the prose. Canvas: TeaPage.dc.html hero, "Як заварюють у спільноті", "Нотатки спільноти".
  - **Seen:** No rating, session count or breadcrumb in the hero. "Sessions with this tea" starts at y=2060 on desktop. On the phone Sessions is at y=3862 and Brewing at y=4127 of 5274.
  - **Steps:** Open `/teas/sencha` (2 public sessions). On a phone, measure where sections start on `/teas/earl-grey`.

- [ ] **Filters fill the first phone screen, with no tea visible** `tea-base`
  - **Page:** `/teas`
  - **Viewport:** phone
  - **What to do:** Make filters compact so results show early, as in MFeed and MForum.
  - **Seen:** The first card starts at about y=617 and is cut off by the tab bar, so only its image top shows.
  - **Steps:** Open `/teas` at 375x812.

- [ ] **Letter jump hides the letter under the sticky header** `tea-base`
  - **Page:** `/teas/a-z`
  - **Viewport:** both
  - **What to do:** Add `scroll-margin-top` to the letter headings and a "back to letters" link.
  - **Seen:** The heading lands at top=0 behind the header (bottom 69 to 73px). There is no way back to the letter row on a 15,930px phone page other than scrolling.
  - **Steps:** On `/teas/a-z` click K (phone) or M (desktop).

- [ ] **Search ranking ignores how well known a tea is, and traditional characters match less** `tea-base`
  - **Page:** `/search`
  - **Viewport:** both
  - **What to do:** Rank famous teas such as Da Hong Pao, Ali Shan or Tie Guan Yin early, and match traditional and simplified forms alike.
  - **Seen:** First rows for "oolong" are Mi Xiang Oolong, Nepal Oolong, Doi Mae Salong No. 12 and No. 17 and Vietnamese Oolong, mostly with no photo. 鐵觀音 returns 1 tea against 3 for 铁观音.
  - **Steps:** Open `/search?q=oolong`. Compare the two spellings.

- [ ] **Ten explainer articles are listed as teas** `tea-base`
  - **Page:** `/teas/style-*`, `/teas/a-z`, tea grids
  - **Viewport:** both
  - **What to do:** Separate explainers from teas, or at least do not offer them as something to log.
  - **Seen:** "CTC vs Orthodox", "Aged Teas", "Bug-Bitten Teas", "Charcoal Roasting" and others (10 in all) count in "441 teas", appear in grids under a category (Aged Teas is "Sheng pu-erh", form "Cake"), and have "Log this tea" and brewing parameters.
  - **Steps:** Open `/teas/a-z`, `/teas/style-ctc-vs-orthodox`, `/teas/style-aged-teas`.

### Errors, admin and static pages

- [ ] **Every 403 says "That form has expired"** `diary` `admin-static` `security`
  - **Page:** `/admin*` as non-admin, `/sessions/{id}/edit` as non-owner, any 403
  - **Viewport:** both
  - **What to do:** In `prolyv/app.py` `ERROR_TEXT[403]`, use `exc.detail` (`csrf_failed` or other) to choose the message. Add a plain "You don't have access to this page" for non-CSRF refusals. The 403 itself is correct.
  - **Seen:** "Error 403 / That form has expired / Reload the page and try again." with a "Back to login" button, even with no form involved. Reloading never helps.
  - **Steps:** Sign in as demo_user or olena_tea and open `/admin`, `/admin/users` or `/admin/posts`. Or open another user's public session at `/edit`, or POST delete to it.

- [ ] **Every error page's main button is "Back to login", even when signed in, and the 404 copy is odd** `admin-static` `people` `blog`
  - **Page:** Error pages (400, 403, 404, 429)
  - **Viewport:** both
  - **What to do:** `error.html` is hard-coded. Make Home the primary button and show login only to signed-out visitors. Use generic 404 copy, with the account wording only on `/u/{handle}`.
  - **Seen:** The primary gradient button says "Back to login" and bounces a signed-in user to `/`. The 404 reads "The page you asked for does not exist, or the account was deleted." on a mistyped URL or missing tea.
  - **Steps:** Signed in as karyna, open `/nonexistent`, `/blog/nonexistent-post` or `/u/nonexistent_person`.

- [ ] **Admin hub cards run title and count together: "Reports0 open", "Users5 accounts"** `admin-static`
  - **Page:** `/admin`
  - **Viewport:** both
  - **What to do:** The `.promo-title` `display:block` rule is scoped to `.tea-base-promo`. Extend it to `.admin-hub`.
  - **Seen:** Reports, Users, Tea base, Blog posts and Discussions render inline with no space. Only Sessions, which has a `<br>`, is correct.
  - **Steps:** Open `/admin` as karyna.

- [ ] **Tea form saves impossible brewing values and silently drops bad input** `admin-static`
  - **Page:** `/admin/teas/new`, `/admin/teas/{slug}/edit`
  - **Viewport:** both
  - **What to do:** Validate numeric ranges in `read_tea_form` and report bad sources and seconds instead of dropping them.
  - **Seen:** Saves with "Saved as a draft". The public card shows "-5 ml water", "999 °C temperature" and "1 steeps". Grams, popularity (stays 3), bad seconds and the bad source line vanish with no message.
  - **Steps:** Create a tea with grams `abc`, temperature 999, water -5, steeps 0, seconds `x, 10, y`, popularity 99 and a non-URL line in Sources. Save.

- [ ] **Drafts are buried on the last page and can't be filtered** `admin-static`
  - **Page:** `/admin/teas`
  - **Viewport:** both
  - **What to do:** Reverse the status sort or add a draft filter, and link the stat to it.
  - **Seen:** The "AAA" draft was the last row of page 9 of 9. The "1 drafts" stat isn't clickable.
  - **Steps:** Create a draft called "AAA ..." and open `/admin/teas`.

- [ ] **Admin searches with no results show nothing at all** `admin-static`
  - **Page:** `/admin/teas?q=`, `/admin/users?q=`
  - **Viewport:** both
  - **What to do:** Add the existing card empty block to both templates, with a way to clear the search.
  - **Seen:** Blank below the search box. Teas still shows "441 published", users shows "Users · 0".
  - **Steps:** Open `/admin/teas?q=zzzzqq` and `/admin/users?q=zzzzqq`.

- [ ] **Admins can't add photos, and the UI tells them to run a command-line script** `admin-static`
  - **Page:** `/admin/teas/{slug}/edit`
  - **Viewport:** both
  - **What to do:** Add a URL or upload field with a credit input, or at least reword the message. The team adds teas itself.
  - **Seen:** "No photos. Add them to the card file and re-run scripts/import_teas.py --only <slug>." Photos can only be made primary or removed.
  - **Steps:** Create or edit a tea with no photos and read the Photos section.

- [ ] **"Restrict" is offered on accounts it can't or shouldn't apply to** `admin-static`
  - **Page:** `/admin/users`
  - **Viewport:** both
  - **What to do:** Hide the button for admin handles and for the reserved prolyv account.
  - **Seen:** On karyna: "Restrict @karyna and sign them out?", then "Admins can't be restricted here." @prolyv can be restricted ("@prolyv is restricted and signed out"); its posts stayed visible. Otherwise the feature works.
  - **Steps:** On `/admin/users` click Restrict on your own @karyna row and accept. Then do the same on @prolyv.

- [ ] **"Start your diary" sends signed-in users to the home page** `admin-static`
  - **Page:** `/about`
  - **Viewport:** both
  - **What to do:** Use `/diary` when `current_user` is set, or `/login?next=/sessions/new` otherwise.
  - **Seen:** The link is hard-coded to `/login`, which redirects a signed-in user to `/`.
  - **Steps:** Signed in, open `/about` and click "Start your diary".

- [ ] **Privacy page leaves out hot-linked Wikimedia images and may send requests to a public forum** `admin-static`
  - **Page:** `/privacy`
  - **Viewport:** n/a
  - **What to do:** Add Wikimedia Commons to the list, set CONTACT_EMAIL for launch, and add a "last updated" date and the operator's name.
  - **Seen:** Photos load from upload.wikimedia.org in the visitor's browser, but "Other services" lists only Google, Telegram, Google Fonts, TikTok/Instagram embeds and a mail provider. If CONTACT_EMAIL is unset (the default, and this copy), privacy and deletion requests are told to "post in Discussions", which is public.
  - **Steps:** Read "Other services" and "Contact" on `/privacy`, then check image URLs in `tea_images` and on tea cards.

### Security hardening

- [ ] **The magic-link POST has no CSRF check, so a third-party page can sign a visitor into the attacker's account** `security`
  - **Page:** `/auth/email/verify`
  - **Viewport:** n/a
  - **What to do:** In `prolyv/web/routes_auth.py:249`, add a double-submit token to `auth/email_confirm.html` (a cookie set on the GET, as the anonymous `/auth/email/start` form does).
  - **Seen:** 303 to `/settings` with `prolyv_session` set. An auto-submitting form on any site logs the visitor in as the attacker, and diary entries then go to the attacker's account. From code, it also revokes any session the browser already had (not run). The token itself is single-use and expires.
  - **Steps:** On a private copy, request a link for an attacker-owned email. POST `/auth/email/verify` with `token=<token>`, no cookies, `Origin: https://evil.example` and `Sec-Fetch-Site: cross-site`.

- [ ] **The DEV_LOGIN guard fails open when BASE_URL is missing, because the default is localhost** `security`
  - **Page:** Config: BASE_URL default, `prolyv/config.py`, `prolyv/web/routes_dev.py`
  - **Viewport:** n/a
  - **What to do:** Make `base_url` required with no default, or have `dev_login_enabled` also compare `request.url.hostname`.
  - **Seen:** 303 with a session cookie, then `/admin` 200. With an https BASE_URL the guard works. This takes two config mistakes at once. From code, the same default also shows magic links on the page when SMTP is unset.
  - **Steps:** On a private copy, start with `DEV_LOGIN=1`, `ADMIN_HANDLES=karyna` and no BASE_URL. GET `/dev/login/karyna` with `Host: prolyv.example.com`, then `/admin`.

---

## Polish

- [ ] **Malformed URLs show a 400 "Something was off" page instead of the 404 or page 1** `diary` `forum` `tea-base`
  - **Page:** `/sessions/notauuid`, `/discussions/abc`, `/discussions?page=abc`, `/teas?page=abc`
  - **Viewport:** both
  - **What to do:** Show the "Nothing steeping here" 404 for bad IDs, and treat a bad page number as page 1, as `page=0` and `page=999` already are.
  - **Seen:** Error 400 "Something was off with that request".
  - **Steps:** Open those URLs signed in.

- [ ] **Splash screen from the canvas is not implemented** `shell-feed`
  - **Page:** Splash
  - **Viewport:** phone
  - **What to do:** Add the Cup, "Brewing your diary…" and progress dots. Canvas: Splash.
  - **Seen:** No such screen or text in the code. The manifest only sets a background colour.
  - **Steps:** Load `/` on the phone and search templates, static files and routes for "Brewing your diary".

- [ ] **Phone shows a footer the design does not have, with small tap targets** `shell-feed`
  - **Page:** Site footer
  - **Viewport:** phone
  - **What to do:** Remove it (MFeed ends with the tab bar), or make links about 44px tall.
  - **Seen:** Links 21px tall, tightly packed in two rows above the tab bar.
  - **Steps:** Scroll to the end of `/` at 375.

- [ ] **Very long tea names make a huge hero** `diary`
  - **Page:** `/sessions/{id}`
  - **Viewport:** both
  - **What to do:** Clamp or shrink the title.
  - **Seen:** 7 lines on desktop and over 13 lines (464px at 34px) on the phone, pushing actions off screen. The name is cut at 160 characters mid-word ("Twice O").
  - **Steps:** Save a 160-character tea name with spaces.

- [ ] **Small session comment issues: misleading length message, delete without confirm** `diary`
  - **Page:** `/sessions/{id}` comments
  - **Viewport:** both
  - **What to do:** Show a message about length, and add `data-confirm` to the comment delete form, as session delete has.
  - **Seen:** A 1-character comment gets "Write a comment first." The delete form deletes on first click and uses a lowercase underlined link style.
  - **Steps:** Post a 1-character comment. Press "delete" on a comment.

- [ ] **No active tab on the phone while logging a tea** `diary`
  - **Page:** `/sessions/new`
  - **Viewport:** phone
  - **What to do:** Highlight "Diary", as in MNewSession (MobileNav current=diary) and the desktop header.
  - **Seen:** No tab is highlighted.
  - **Steps:** Open Log a tea on the phone.

- [ ] **Search inside a section gives weak feedback, and chips drop the query** `forum`
  - **Page:** `/discussions/s/{slug}?q=`
  - **Viewport:** both
  - **What to do:** Keep or clearly clear the query, add a "Search:" label, and word the empty result so it does not suggest the forum is empty. Canvas: Forum.
  - **Seen:** The chip discards the query. The H1 stays the section name. No-match says "Log in to start one" or "Start the first one", linking to New topic without the current section.
  - **Steps:** Search from `/discussions`, click a section chip. Then search a nonsense word inside a section, signed out.

- [ ] **Reply and topic bodies are silently cut at 20000 characters** `forum`
  - **Page:** `/discussions/new`, reply form
  - **Viewport:** both
  - **What to do:** Add a counter or a visible validation message. The textarea has no `maxlength`; the title has `maxlength=160`.
  - **Seen:** Saved truncated with no message.
  - **Steps:** POST a 25000-character body.

- [ ] **An author can mark their own reply helpful by direct POST, which also feeds the Solved logic** `forum`
  - **Page:** POST `/discussions/{id}/replies/{id}/mark`
  - **Viewport:** n/a
  - **What to do:** Reject it, matching the UI, which hides the button.
  - **Seen:** 303, and "Marked helpful by the author" appears on the author's own reply.
  - **Steps:** As the topic author, POST `/mark` for your own reply.

- [ ] **Notifications about a deleted topic lead to the 404 page** `forum`
  - **Page:** `/notifications`
  - **Viewport:** both
  - **What to do:** Remove or mark the dead notification.
  - **Seen:** The notification stays and its target returns 404.
  - **Steps:** Receive a reply notification on a topic, delete the topic, follow the stored link.

- [ ] **Markdown typographer setting has no effect** `blog`
  - **Page:** `/blog/{slug}`
  - **Viewport:** both
  - **What to do:** Enable the replacements and smartquotes rules, or remove `typographer: true`.
  - **Seen:** Straight quotes and `--` come through unchanged.
  - **Steps:** Publish text containing `"smart quotes" -- dash`.

- [ ] **Hide and unhide on a comment jumps back to the top of the post** `blog`
  - **Page:** `/blog/{slug}` comments
  - **Viewport:** both
  - **What to do:** Pass `path#comments` as `next` to `hide_button` and `report_button`.
  - **Seen:** Redirect to the bare post URL with scrollY 0, while comment post and delete return to `#comments`.
  - **Steps:** As admin, click hide on a comment far down a long post.

- [ ] **Tight spacing around the Comments heading** `blog`
  - **Page:** `/blog/{slug}`
  - **Viewport:** both
  - **What to do:** Add margin to `.h2#comments`.
  - **Seen:** "Comments" sits almost against the card above and the textarea below, tighter than the gap before "Brewed along".
  - **Steps:** Scroll to the end of a post with no brew-alongs.

- [ ] **Stat labels are not singular for a count of 1** `people`
  - **Page:** `/u/{handle}`
  - **Viewport:** both
  - **What to do:** Pluralise the hardcoded labels.
  - **Seen:** "1 followers". "1 public sessions" and "1 helpful replies" behave the same way.
  - **Steps:** Open a profile with one follower.

- [ ] **Stat numbers alternate two colours instead of the canvas's four gradients** `people`
  - **Page:** `/u/{handle}`
  - **Viewport:** both
  - **What to do:** Use pink-orange, mint-blue, purple-pink and yellow-orange. Canvas: Profile.dc.html.
  - **Seen:** Pink, mint, pink, mint.
  - **Steps:** Compare the stat cards with the canvas.

- [ ] **Follow state label and Report link differ from the design or are too small** `people`
  - **Page:** `/u/{handle}`
  - **Viewport:** both
  - **What to do:** Use the label "Following" and a tap target of about 44px. Canvas: MProfile.dc.html.
  - **Seen:** "Following ✓". Report is a 19.5px text link floating above the Sessions heading.
  - **Steps:** Press Follow, then look at Report on another person's profile on the phone.

- [ ] **The bell still shows the unread count on the page that clears it** `people`
  - **Page:** `/notifications`
  - **Viewport:** both
  - **What to do:** Clear the badge on arrival.
  - **Seen:** The badge showed 3 on first render and was gone on the next page load.
  - **Steps:** With unread notifications, open `/notifications`.

- [ ] **Linking an e-mail uses sign-in wording for someone already signed in** `people`
  - **Page:** `/settings`, link e-mail page
  - **Viewport:** both
  - **What to do:** Use "Link this e-mail to your account".
  - **Seen:** Title "Sign in", with "Sign-in link", "Continue as qa-demo@example.test" and "Sign in to Prolyv". The link does work.
  - **Steps:** In Settings submit Link e-mail, then open the link.

- [ ] **A single saved tea card fills only half the phone width** `people`
  - **Page:** `/saved`
  - **Viewport:** phone
  - **What to do:** Make the card fill the row or sit in a balanced layout.
  - **Seen:** The card is in the left column with the right half empty.
  - **Steps:** Save one tea and open `/saved` at 375.

- [ ] **A-Z order is case-sensitive** `tea-base`
  - **Page:** `/teas/a-z`
  - **Viewport:** both
  - **What to do:** Sort case-insensitively, so Cameron Highlands comes before CTC.
  - **Seen:** "CTC vs Orthodox" sorts before "Cameron Highlands Black Tea" and "Ceylon CTC".
  - **Steps:** Open `/teas/a-z`, letter C.

- [ ] **Awkward labels** `tea-base`
  - **Page:** `/teas/c/tisane`, `/teas/c/heicha`, `/teas/{slug}`
  - **Viewport:** both
  - **What to do:** Use natural wording.
  - **Seen:** "Tisane teas" (a tisane is by definition not tea) and "Heicha teas". "Sold as · vintages" appears on all 441 cards, including Earl Grey, whose text says it has no harvest year, and Chamomile.
  - **Steps:** Read the H1 on the category pages and the headings on `/teas/earl-grey` and `/teas/chamomile`.

- [ ] **List page description and URL leftovers** `tea-base`
  - **Page:** `/teas`
  - **Viewport:** both
  - **What to do:** Make the meta description accurate and drop empty parameters from URLs.
  - **Seen:** The description says "441 teas with photos ... and how each one is sold", but 186 have photos and shops are parked. An empty result gives "0 teas with photos". The live filter pushes `?q=&origin=&form=&sort=popular`.
  - **Steps:** View the meta description on `/teas` and `/teas?q=zzzzzz`. Use the live filter and read the URL.

- [ ] **No browse link is marked current on origin and category pages** `tea-base`
  - **Page:** `/teas/o/{key}`, `/teas/c/{cat}`
  - **Viewport:** both
  - **What to do:** Mark one browse link as current.
  - **Seen:** Search, Types, Countries and A-Z are all inactive.
  - **Steps:** Open `/teas/o/china` or `/teas/c/oolong` and look at the browse links.

- [ ] **Small admin inconsistencies** `admin-static`
  - **Page:** `/admin/posts`, `/admin/reports`
  - **Viewport:** both
  - **What to do:** Link the eyebrow in `posts.html` and add a flash to `admin_report_resolve`.
  - **Seen:** "Admin" is plain text on `/admin/posts` but a link on `/admin/teas`, `/admin/users` and `/admin/reports`. Mark resolved gives no flash, while Hide and Show again do.
  - **Steps:** Compare the eyebrow links across admin pages, then click Mark resolved on a report.

- [ ] **Admin list rows change layout depending on name length** `admin-static`
  - **Page:** `/admin/teas`, `/admin/users`
  - **Viewport:** phone
  - **What to do:** Force the name onto its own full-width line in `.admin-list` at phone width.
  - **Seen:** Short names ("Awabancha", "Bai Ji Guan", "@prolyv", "@karyna") put meta beside the name and drop the button left on its own line. Longer names have meta below and the button on the right.
  - **Steps:** Open `/admin/teas` or `/admin/users` at 375.

- [ ] **Rules say "Anyone can press Report", but signed-out visitors have no Report control** `admin-static`
  - **Page:** `/rules`
  - **Viewport:** both
  - **What to do:** Change the copy to "Any member can press Report".
  - **Seen:** No Report control when signed out. Signed-in users see it.
  - **Steps:** Signed out, open a topic and look for Report.

- [ ] **Clumsy repeated sentence in a launch guide** `admin-static`
  - **Page:** `/blog/storing-tea-at-home` (`seed_prod.py`)
  - **Viewport:** n/a
  - **What to do:** Reword to: the yellow "golden flowers" that are an intended part of Fu brick.
  - **Seen:** It calls them "a normal part of Fu brick, which are an intended part of how that tea is made".
  - **Steps:** Read the "Pu-erh and other dark teas" section.

- [ ] **Signed-in pages send no Cache-Control header** `security`
  - **Page:** `/diary`, `/settings`, `/settings/export`, `/notifications`, `/saved`
  - **Viewport:** n/a
  - **What to do:** Send `Cache-Control: no-store`, or at least `private`, from SecurityHeadersMiddleware when a session cookie is present, or on the render path for signed-in users.
  - **Seen:** No header. Headers only were checked; the back button was not tested.
  - **Steps:** `curl -i` those routes with a session cookie.

- [ ] **The rate-limit response carries no security headers** `security`
  - **Page:** 429 from `/login` and `/auth/*`
  - **Viewport:** n/a
  - **What to do:** In `prolyv/app.py:70-73`, add SecurityHeadersMiddleware last so it wraps the rate limiter.
  - **Seen:** Only date, server, content-type, content-length and retry-after. The body is fixed plain text, so the risk is negligible.
  - **Steps:** GET `/login` more than 30 times in a minute and inspect the 429.

---

## Not checked

**Across areas**

- Real phones and Safari: all phone results come from mobile emulation or headless Chromium, so exact tab bar position, native selects and dialogs, and the native share sheet are unconfirmed. Tablet widths and PWA install were not tested.
- Production: the VM, Caddy, systemd and `.env` settings (SOCIAL_*, CONTACT_EMAIL, BASE_URL) were not checked. Footer social links may differ there.
- Real Telegram and Google sign-in, and real e-mail delivery (no SMTP here). This also covers avatar photos from a provider and a live OIDC flow.
- Lighthouse, search-engine validation of the sitemap, slow-network timing and Wikimedia rate limits.

**Security**

- Whether Telegram login is on in production and whether the admin has Telegram linked. Both affect the first blocker.
- X-Forwarded-For spoofing of the rate limiter: a spoofed header got past a full bucket on direct uvicorn loopback. Not reported, because in production it depends on Caddy overwriting the header and port 8010 staying closed. Please verify on the VM.
- The memory finding under MemoryMax on the VM, uploads larger than 11 MB against the Caddy 60 MB limit, and e-mail response timing.
- XSS through admin-only fields was checked by code reading only.

**Feed and sign-in**

- A directed test that a followed user's private session stays out of the Following tab.
- The 15-minute link expiry through the browser path and the e-mail rate limit, both in the shell pass. The security pass covered both by HTTP.

**Diary and comments**

- Owner self-notification on comments (quota reached), author deleting their own comment, and admin hide clicks on sessions.
- Camera picker, HEIC, a photo over 10 MB in the browser, and the timer with the screen locked.
- Enter in other single-line inputs such as the tea name.

**Forum**

- Pagination inside a section or in search results, attach with no public sessions or more than 20, the deleted-account chip in threads, deleting an attached session, recovery after the rate-limit window, and where the Report form lands deep in a thread.

**Blog**

- A real TikTok or Instagram video. The Instagram iframe with a fake ID stayed about 2px tall and full phone width, so final layout is unverified.
- The `/blog` empty state, a real login returning from Brew along or Save, the rendered signed-out post page, and posts or sessions by deleted accounts.

**People**

- A successful "Use Telegram photo", the 100-notification cap, more than 5 topics on a profile, banned-user profiles, a long URL in a bio, the bell's 99+ state, and the Download my data button in a browser (checked with curl).

**Tea base and admin**

- Encyclopedia fields: 0 of 441 pages show them. Un-saving a tea. Wrong-variety photos, since review was at thumbnail size.
- Photo actions on an imported tea, reporting a user or session, `/admin/users` pagination, and a restricted user signing in through a real provider.

## Test data left on the QA copy (8100)

Expect some oddities on this copy. It is a throwaway database.

- **Accounts and notifications:** account `qa_shell_tester1`; one saved session on demo_user; three follow notifications on olena_tea.
- **Sessions and topics:** a session with an XSS payload as tea name (tea_mike); session `b66cd4df` and topic `92f7fb37` "QA attach" (prolyv); forum topics, replies and reports, including the 23-reply thread.
- **Blog:** posts titled QA*, Vid*, Dup one, Weird slug, Slug new, Bad tea slug, BadTea publish, x-check-tea, Чай и дзен, !!!???, Very long title…, plus many drafts; QA sessions on `first-gongfu-session`; 20 "quota N" comments on `keeping-a-tea-diary`.
- **Moderation state:** one report on a deleted topic remains; another tester's reply report was marked resolved.
- **Shared-cookie noise:** testers shared a sign-in cookie, so occasional "That form has expired" pages during testing were collisions. They are not reported as bugs, but Follow, Save and logout were re-verified in isolation.
