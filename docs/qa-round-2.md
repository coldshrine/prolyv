# Prolyv QA round 2: merged findings for the developer

**To fix: 0 blockers, 9 major, 45 minor, 25 polish (79 items).** A further 6 questions for the owner are listed at the end.

Viewports: desktop is Chromium at 1440; phone is Playwright WebKit with the iPhone 13 profile, unless an item says otherwise. No tester used a real iPhone.

Screenshots named in items are under `/private/tmp/claude-501/-Users-caroline-Desktop/2380e5e0-eebe-4f25-bafa-c8ed6aecb74c/scratchpad/qa2/`, in the tester's area folder (for example `diary/`, `shell/`, `people/`).

## Testers' verdicts

| Area | Round 1 fixed | Not fixed | Show it to the owner? |
|---|---:|---:|---|
| Shell and feed | 7 | 0 | **Yes.** No blocker or major. Check `SOCIAL_TIKTOK`, `SOCIAL_INSTAGRAM` and `CONTACT_EMAIL` on the server first. |
| Diary | 13 | 0 | **Yes, once the double save is fixed.** |
| Forum | 4 | 1 | **Nearly.** Not until Compare side by side stops returning 404. |
| Blog | 5 | 0 | **Nearly.** No blockers or majors, but first fix "Steep by step", the phone cover cups and the odd notice, set the two social settings and publish one real video post. |
| People | 8 | 0 | **Yes, once the WebKit tea card photo is fixed.** |
| Tea base | 8 | 1 | **Not as finished yet.** The language switch does not exist, about half the cards are boilerplate, and Back after Show more and the first phone card's photo band need fixing. |
| Admin and security | 10 | 2 | **Security work and back office yes, launch sign-off no,** until the upload memory hole and the silent unpublish on the tea form are fixed. |

Round 1 items still open: Compare side by side (topic page rebuild), search All tab (partly fixed), upload memory, Russian text (partly fixed). Testers' totals are 55 fixed and 4 not fixed; the shared long-string item is counted in more than one area, and it is fixed in all of them.

## Blocker

None reported.

## Major

- [ ] **Double-clicking Save creates two identical sessions** (diary)
  - Page: /sessions/new
  - Viewport: desktop; the phone top bar Save uses the same form, but a real double tap was not tried
  - What to do: Disable the submit buttons on first submit in session.js, including `.phonebar-save`. A one-time form token on the server would also stop slow-network retries.
  - Steps: Sign in as tea_mike. Open /sessions/new, type the tea name DblReal, double-click Save session (Playwright dblclick), open /diary. Two DblReal cards are listed. Two clicks from script do the same (DoubleTap as karyna).

- [ ] **Compare side by side opens a 404** (forum)
  - Page: /discussions/{id}, any topic with 2 or more attached sessions
  - Viewport: both
  - What to do: `prolyv/templates/forum/topic.html:178` links to /sessions/compare?a=...&b=..., which does not exist. Link to /sessions/{a}/compare?with={b}. Decide what a signed-out visitor gets, because that route uses RequiredUser. Check the compare page for two sessions that are both someone else's.
  - Steps: Attach a session to a topic as demo_user. Reply with an attached session as olena_tea. Open the topic and click Compare side by side in Sessions in this thread. "ERROR 404 Nothing steeping here" appears, signed in on both viewports and signed out on desktop.

- [ ] **A small image takes the server far past its 300 MB limit, and any signed-in user can send it** (admin-security; round 1 not fixed)
  - Page: /sessions/new, /sessions/{id}/edit (photo upload)
  - Viewport: n/a
  - What to do: `draft()` does not save memory on progressive JPEGs, because the whole coefficient buffer is still allocated. Refuse progressive JPEGs above about 12 MP, lower `MAX_PIXELS_OTHER` to about 8 MP for PNG and WebP, then re-measure with these three files.
  - Steps: On a private copy, restarting before each case (base RSS 104 MB), POST /sessions/new as tea_mike with one solid-colour photo while sampling process RSS. All three are accepted with a 303 and memory stays high afterwards, against `MemoryMax=300M`: progressive JPEG 7700x7700 4:4:4, 680 KB: 542 MB. Lossless WebP 4470x4470: 462 MB. RGBA PNG 4470x4470, 84 KB: 306 MB. Normal photos are fine (baseline JPEG of that size 201 MB, four 12 MP JPEGs 189 MB), and the round 1 PNG is now refused at 107 MB. Measured on macOS, not the VM.

- [ ] **Any validation error on the tea form unticks Published, so the next save takes the tea off the site** (admin-security)
  - Page: /admin/teas/{slug}/edit, /admin/teas/new
  - Viewport: both
  - What to do: `read_tea_form` does not put `published` into the form dict. Add `published=bool(data.get('published'))` and cover it with a test.
  - Steps: As karyna, open /admin/teas/shu-puerh-7581-brick/edit. Set Temperature to 300 and save; Published comes back unticked. Correct to 95 and save; it says "Card saved." Signed out, /teas/shu-puerh-7581-brick is now 404. On /admin/teas/new, an invalid colour corrected and saved ends in "Saved as a draft."

- [ ] **In WebKit, tea card photos sit too low, leaving a dark band and cutting off the bottom of the photo** (people, tea-base; merged)
  - Page: /saved (Teas section), /teas including its very first card, /teas/o/japan and other grids
  - Viewport: phone mainly; WebKit also shows the offset at 1440, and desktop Chromium does not crop as intended
  - What to do: In `app.css:284-285`, `.tea-card-media` is `display:grid` with `place-items:center` and an aspect-ratio, and its img has `height:100%`. Give the img `position:absolute; inset:0`, or drop the grid centring for images. Then confirm `object-fit: cover` crops on desktop too.
  - Steps: Open /saved with a tea saved, or /teas (first card, Ali Shan High Mountain Oolong), and measure `.tea-card-media` against its img. Offset is 104px in the 267px box on /saved, 50px on /teas on the phone and 83px in WebKit at 1440; Chromium is 0. After three Show more presses, 13 of 60 photo cards were offset (50px for 3:4, 21px for square), including Peppermint, Sencha, Gyokuro, Shincha and Hong Kong Milk Tea. In desktop Chromium the Ali Shan img is 378px tall in a 213px box and shows mostly sky. See `people/m_saved_b.png`.

- [ ] **Language switch does not exist: ?lang=ru and ?lang=zh show the English text** (tea-base)
  - Page: /teas/{slug}?lang=ru, ?lang=zh
  - Viewport: both
  - What to do: Run the translation pass and import it, or tell the owner this ships English-only. `i18n` is {} on 2523 of 2523 teas, and no file in data/teas or data/tea-research/master has an i18n key.
  - Steps: Open /teas/da-hong-pao, /teas/sencha, /teas/earl-grey or /teas/apricot-tea, then add ?lang=ru or ?lang=zh. There is no switch, the text is English, there are no hreflang tags, and the da-hong-pao HTML is 21717 bytes on all three URLs.

- [ ] **Most cards are boilerplate that repeats "not well documented" up to five times** (tea-base)
  - Page: /teas/{slug} thin cards, e.g. /teas/apricot-tea, /teas/lu-xiang-ming, /teas/baokang-lu-cha
  - Viewport: both
  - What to do: On low-confidence cards, hide History, Harvest and How it is sold when the text is generic, and keep one "little is documented" line.
  - Steps: Read /teas/apricot-tea from lead to Sources (about 3966px on the phone). It says so in the lead, Harvest, How it's made, History and a note. 1279 of 2523 cards have this kind of lead, two identical sentences cover 681 and 595 cards, "Little is documented" is in the History of 1688 cards, and 885 descriptions contain "With little written about it". The flavoured apricot blend also gets "Black teas are normally sold by harvest year".

- [ ] **Show more is lost on Back: the list drops to 24 cards at the wrong scroll position** (tea-base)
  - Page: /teas, /teas/c/{cat}, /teas/o/{key}
  - Viewport: both
  - What to do: Have Show more push ?page=N and render pages 1..N on load, or restore the appended cards through the htmx history snapshot.
  - Steps: On /teas press Show more twice (72 cards), open the card at position 51 (Gyokuro), press Back. The list has 24 cards and the URL stays /teas. Scroll goes from 4429 to 2230 on desktop and 8893 to 4953 on the phone.

- [ ] **Russian names show on tea cards across the English-only site** (admin-security; round 1 partly fixed)
  - Page: /teas and every tea card, /teas/{slug}, /admin/teas/new
  - Viewport: both
  - What to do: Keep `name_ru` for search but stop rendering it (`partials/macros.html` lines 25 and 127, `teas/detail.html` line 47), or get an explicit decision from the owner; it is not in the decisions file, and the site says "Made in Ukraine". Review the admin "Russian name" field with its Cyrillic placeholder at the same time.
  - Steps: curl /teas and read `.tea-card-native`, e.g. "白毫银针 · Бай Хао Инь Чжэнь". Query teas where `name_ru` is set: all 2523. Open /admin/teas/new. Russian search now works, so only the display is open.

## Minor

- [ ] **Launch settings are missing, and the blog promises videos it does not have** (blog, shell-feed; merged)
  - Page: /blog, plus sidebar and footer socials and Contact
  - Viewport: both
  - What to do: Set `SOCIAL_TIKTOK`, `SOCIAL_INSTAGRAM` and `CONTACT_EMAIL` on the server and publish at least one real video post before opening. The pills, the "home of our TikTok and Instagram" line, the Find us card and Contact all depend on these settings.
  - Steps: Open /blog and press Video breakdowns: "Nothing in this category yet." All 5 seeded posts are guides and no pills are drawn. The pills were not tested with the settings on.

- [ ] **Footer tagline drops the line that ties the site to TikTok and Instagram** (admin-security)
  - Page: footer, all pages
  - Viewport: both
  - What to do: Confirm the line appears once the social settings are set (the shell-feed tester reports it depends on them; the admin tester did not test with them set). If it does not, restore the canvas copy.
  - Steps: Compare with Footer.dc.html, which adds "the home of our TikTok and Instagram". The page reads "A tea diary, forum and blog. Log your sessions, talk them through and share."

- [ ] **Page text shows through the sticky header and tab bar in WebKit** (shell-feed, diary, people; merged; unconfirmed on a device)
  - Page: all phone pages; reported on /, /diary, /sessions/new, /sessions/{id}, /u/{handle}
  - Viewport: phone
  - What to do: Check on a real iPhone. If it shows there, raise the alpha of both bars to about 0.95 (`.pf-topbar` is `rgba(7,8,26,0.85)`). CSS already sets blur(16px) or blur(18px), Chromium blurs correctly, and the blog tester reports Playwright WebKit does not draw this blur, so this may be an emulation artefact.
  - Steps: Open / in WebKit and scroll; "Olena logged Da Hong Pao" is legible behind the logo and tab labels. See also `diary/p_diary1.png`. On /u/olena_tea, "Follow" or "Log a tea" shows behind the tab bar on the first screen.

- [ ] **Own profile contradicts itself: stat says 2 sessions logged, the line below says 6** (people)
  - Page: /u/{handle}, own profile
  - Viewport: both
  - What to do: In `routes_account.py` `profile_page` `stats[0]`, show `logged_count` to the owner, or label the public count "public sessions" for everyone. This is a privacy choice, so the owner should decide.
  - Steps: Sign in as demo_user and open /u/demo_user. The tile reads "2 sessions logged"; the line reads "6 logged · 2 shared."

- [ ] **Story cup is not the brand cup: no spiral and no inner ring** (diary)
  - Page: /sessions/{id}/story.png, /diary/month.png
  - Viewport: n/a
  - What to do: Draw the spiral and inner ring in `story.py` `_cup`, or composite a rendered cup image. This is the one image that leaves the site.
  - Steps: Open any story.png or month.png and compare with the session page cup or Story.dc.html. See `diary/story.png`.

- [ ] **Main pages have no Open Graph tags, and home and forum have no description** (admin-security)
  - Page: /, /discussions, /blog, /teas, /about
  - Viewport: n/a
  - What to do: Add the tags, following the pattern tea pages already use, so links pasted into bios and DMs preview properly.
  - Steps: curl each page and grep for meta description and og:. / and /discussions have neither; /blog, /teas and /about have a description but no og: tags.

- [ ] **Privacy requests are sent to a public forum when CONTACT_EMAIL is unset** (admin-security)
  - Page: /privacy
  - Viewport: both
  - What to do: Set `CONTACT_EMAIL` and confirm the text changes (not checked), and provide a private channel by default.
  - Steps: Read Contact on /privacy: "Questions or requests: post in Discussions and an admin will follow up."

- [ ] **With BASE_URL unset, a handle makes someone admin on a real domain** (admin-security)
  - Page: config, `is_admin_user` in `prolyv/auth/accounts.py`
  - Viewport: n/a
  - What to do: Also require `on_this_machine(request)`, or give `BASE_URL` no default. `settings.is_local` never checks the request host. It takes two config mistakes; with an https `BASE_URL` the guard holds.
  - Steps: On a private copy with `ADMIN_HANDLES=karyna` and no `BASE_URL`, sign in as karyna on localhost, then GET /admin with the same cookie and `Host: prolyv.example.com`. It returns 200.

- [ ] **Brew It the Same Way ticks the other person's taste tags as yours** (diary)
  - Page: /sessions/new?from={id}
  - Viewport: desktop
  - What to do: Drop `taste_tags` from `form_from_session` when `as_copy` is true, and consider a note saying whose session is being copied.
  - Steps: As another user, open a session tagged Floral, Honey, Roasted and press Brew it the same way. The tags are ticked and appear in How others will see it. See `diary/d_brew2.png`.

- [ ] **Cancel deletes the whole draft without asking** (diary)
  - Page: /sessions/new
  - Viewport: both
  - What to do: Show the existing confirm dialog when the draft has content. Clearing the draft is correct; only the confirmation is missing.
  - Steps: Fill in a new session, press Cancel (top left on the phone), reopen /sessions/new. There is no dialog and the form is empty.

- [ ] **Reload during a running steep resets the timer** (diary)
  - Page: /sessions/new
  - Viewport: both
  - What to do: Add `startedAt` and `banked` to the draft. The timer already uses Date.now, so it can resume correctly.
  - Steps: Start the timer, wait 2 s, reload. It shows 00:00, STEEP 1 · READY, while the rest of the draft returns. Safari reloading a tab on return may do the same (not tested on a device).

- [ ] **Water stepper clips four-digit values** (diary)
  - Page: /sessions/new
  - Viewport: desktop
  - What to do: Reduce the font for long values, or give the value more room in the 3-across grid.
  - Steps: At 1440, type 1500 in Water. It reads "150(" (scrollWidth 64, clientWidth 53). 12.5 in Leaf nearly touches +. See `diary/d_wide.png`.

- [ ] **Tea picker ranks unrelated teas above real matches** (diary)
  - Page: /sessions/new
  - Viewport: both
  - What to do: Rank exact-word and prefix matches on name and category above fuzzy matches.
  - Steps: Type shu. Dayi 7572 Shu Cake comes sixth, after Da Hong Pao, Jin Xuan Milk Oolong, Pu-erh (Sheng), Bancha and Dayi 7542 Sheng Cake. See `diary/p_suggest.png`.

- [ ] **Photo hint promises uploads the server then refuses** (diary)
  - Page: /sessions/new (photos)
  - Viewport: both
  - What to do: Check count and type in the change handler, and render small previews.
  - Steps: Select 5 photos, or a .txt through script. The hint says "5 photos ready to upload" under "Up to 4". After saving: "Session saved, but a photo was refused", with 4 kept and no way to see which one was dropped.

- [ ] **Suggested next steep goes stale after editing a steep** (diary)
  - Page: /sessions/new
  - Viewport: desktop
  - What to do: Recalculate the suggestion and the + Steep label when a steep is edited or removed.
  - Steps: Log 2, 10, 15. Change the second to 45 and remove the third. It shows 15 s, and 70 s after reload, for the same [2, 45].

- [ ] **A comment typed while over the posting limit is lost on the error page** (diary; from tester notes)
  - Page: /sessions/{id} comments
  - Viewport: not stated
  - What to do: Keep the text and show the limit message, as blog comments and forum topics do. Also check whether session saves and edits should use the same 20-per-10-minutes counter as comments, because they do now.
  - Steps: Reach 20 posts in 10 minutes on an account, then post a comment on a session. The 429 error page replaces the form.

- [ ] **Saving a session over the posting limit lands on a bare error page** (blog)
  - Page: POST /sessions/new, from Add my session or Copy to my diary
  - Viewport: both
  - What to do: In `session_create`, catch the quota error and re-render the form, as `post_comment_create` does.
  - Steps: Reach the limit, then save a session from a post. A full-page "ERROR 429 Slow down a little" appears with only a Home link. Whether the autosaved draft restores was not checked.

- [ ] **Selected section chip is often scrolled off screen** (forum; intermittent)
  - Page: /discussions/s/{slug}
  - Viewport: phone
  - What to do: Set `scrollLeft` again on window load or in requestAnimationFrame, or use scrollIntoView with `inline:'center'`.
  - Steps: Load /discussions/s/where-to-buy, beginners, where-to-buy, teaware, where-to-buy in turn, waiting 1.5 s and reading the chip row's `scrollLeft`. The first two scrolled (786, 726); the last three stayed at 0, with the selected chip at x=1000 or x=715.

- [ ] **Form selects render as thin native controls** (forum)
  - Page: /discussions/new, Report on /discussions/{id}
  - Viewport: phone
  - What to do: Use `appearance:none`, a min-height of 44px or more, and a custom chevron, like the Sort control.
  - Steps: Measure the Section and Attach selects (26px, next to a 54px Title input) and the Report reason select (27px). A real iPhone may draw them a little differently.

- [ ] **Admin's Mark as helpful is labelled as the author's and marks the topic Solved** (forum)
  - Page: /discussions/{id}
  - Viewport: both
  - What to do: Remove `is_admin_view` from the condition on the mark button, or use a different label for moderators.
  - Steps: As karyna, on a topic by demo_user, click Mark as helpful on a reply. It shows "MARKED HELPFUL BY AUTHOR", moves to the top, and the topic becomes SOLVED.

- [ ] **Quoting a deleted account inserts its internal handle** (forum)
  - Page: /discussions/{id} with a deleted author
  - Viewport: both
  - What to do: In topic.html, send an empty `data-quote-author` when `user.deleted_at` is set, and have forum.js drop the @ prefix when it is empty. Replies use the same template code but were not tested.
  - Steps: As another user, click Quote on the opening post. The box receives "> @deleted_0405c3825709: ...", and the handle is in the page HTML.

- [ ] **Most helpful this month ignores the helpful counts shown on replies** (forum)
  - Page: /discussions
  - Viewport: both
  - What to do: `helpful_this_month` counts ReplyVote rows, but the seed sets `helpful_count` with no votes. Seed real votes, or sum `helpful_count` for replies in the period.
  - Steps: On a fresh database, the seeded shu topic shows Helpful · 3 but the rail block is absent. After one real vote on another of Mike's replies, he is listed as "1 helpful".

- [ ] **Heading says "Steep by step" instead of the approved "Steep by steep"** (blog)
  - Page: /blog/{slug} (first-gongfu-session, reading-puerh-wrapper)
  - Viewport: both
  - What to do: Change `h2#steeps-h` in `templates/blog/post.html`.
  - Steps: Open /blog/first-gongfu-session and read the heading above the steep list. Canvas: BlogPost, MBlogPost.

- [ ] **Phone cups sit off-centre: the LATEST cup is clipped and badges cover small cups** (blog)
  - Page: /blog
  - Viewport: phone
  - What to do: The badge and tag spans seem to take part in the cover's layout. Position them absolutely and centre the cup.
  - Steps: Measure `.blog-latest-cover .cup` and `.blog-cover .cup`. LATEST: cover y 292-492, cup y 346-498, so it is cut by 6px. Post card: cup 12px right of centre and 17px down, and the GUIDE badge (y 863-883) overlaps it (y 874). Canvas: MBlog.

- [ ] **Brew-along block makes no sense on guides with no tea or session** (blog)
  - Page: /blog/{slug} for keeping-a-tea-diary, storing-tea-at-home, leaf-water-temperature-starting-points
  - Viewport: both
  - What to do: Show the block, the hero button and the diary line only when the post has a linked tea, or reword them for general guides.
  - Steps: Open /blog/storing-tea-at-home, press Add my session, fill in any tea, make it public and save. The page says "1 person brewed along", with a row reading just "Prolyv / gaiwan".

- [ ] **"Copy to my diary" does not copy the steeps or the rinse** (blog)
  - Page: /blog/first-gongfu-session, then /sessions/new?post=first-gongfu-session
  - Viewport: both
  - What to do: Prefill steep rows from the tea's seconds and tick Rinsed, or rename the button if the times are only timer suggestions.
  - Steps: Press Copy to my diary, tick Show in the community feed, save. The session says "OOLONG · 0 STEEPS"; the post shows 7 steeps (15, 10, 15, 20, 30, 45, 60 s) and "Rinse once".

- [ ] **"Brewing along with" notice breaks into three lines, the last starting with a period** (blog)
  - Page: /sessions/new?post={slug}
  - Viewport: both
  - What to do: `.notice` is a flex container, so wrap the content in one span or use `display:block`.
  - Steps: Press Copy to my diary or Add my session and read the green notice. See `blog/d_copy.png`, `blog/m_copy.png`.

- [ ] **With no brew-alongs, the design's right column disappears and the grid changes shape** (blog)
  - Page: /blog
  - Viewport: desktop
  - What to do: Keep the 2-column grid at all times, or fill the aside with an empty state or a "Start your own diary" card.
  - Steps: At 1440 on seeded data there is no aside and 4 narrow columns. Add one public brew-along and reload: the page becomes 2 columns plus aside. Canvas: Blog.

- [ ] **The "Stats" category tab from the canvas is missing** (blog)
  - Page: /blog
  - Viewport: both
  - What to do: Add the tab, or record the decision to drop it.
  - Steps: Only All, Video breakdowns and Guides appear. /blog?tab=stats silently shows All. Canvas: Blog, MBlog.

- [ ] **Tapping a lower steep changes a cup you can no longer see** (blog)
  - Page: /blog/first-gongfu-session
  - Viewport: phone
  - What to do: Make the cup sticky under the top bar on the phone, or put a small cup in the selected row.
  - Steps: Scroll to the steep list and tap Steep 7. The row highlights, but the cup is off screen (top -349, bottom -144). This affects steeps 5 and up. See `blog/m_steep7.png`.

- [ ] **The All tab drops the Sessions group on the commonest word** (tea-base; round 1 partly fixed)
  - Page: /search?q=tea
  - Viewport: both
  - What to do: Query each kind with its own limit on the All tab.
  - Steps: Open /search?q=tea on All: only Teas, Discussions and Blog appear. The Sessions tab has 10 cards and a Show more.

- [ ] **Plant cultivars are presented as finished black teas** (tea-base)
  - Page: /teas/choibari-27-cultivar and about 392 "-cultivar" style cards
  - Viewport: both
  - What to do: Add a cultivar type or filter them out of default lists, and swap the CTA and sections on those cards. Review the near-duplicates at the same time: qianmei-502 and qianmei-502-black, and osmanthus-oolong and osmanthus-oolong-taiwan, have identical names.
  - Steps: Read /teas/choibari-27-cultivar. About says it is a plant, but the card has a Black eyebrow, Log this tea and tasting and selling text. One description is verbatim on 24 cards, 310 zh names contain Latin letters ("Choibari 27 茶树品种", also in the browser title), and they inflate Black 974.

- [ ] **Region data has silly labels: "China · China", "Teas from Nationwide", "Unknown · Unknown"** (tea-base)
  - Page: /teas/lu-xiang-ming, /teas/genmaicha, /teas/origins, /teas/o/japan, /teas/o/nationwide, /teas/o/upasi, /teas/magorak
  - Viewport: both
  - What to do: Drop a region that equals the country, and extend `NOT_PLACES` with nationwide, various tea regions, upasi, tocklai and unknown.
  - Steps: Read the eyebrow and Origin on /teas/lu-xiang-ming, and the chips on /teas/origins and /teas/o/japan ("Japan 25", "Taiwan 11", "Various tea regions 3"). 227 teas have region equal to country. Open /teas/o/nationwide, /teas/o/upasi and /teas/magorak.

- [ ] **Live filtering leaves the category chips stale, so a chip then drops the filter** (tea-base)
  - Page: /teas after using a dropdown
  - Viewport: both
  - What to do: Add the `.chips` nav to the htmx out-of-band swap next to `#tea-head`.
  - Steps: Choose Origin Japan without reloading, then click Green. The chip still reads "Green 1279" and goes to /teas/c/green, so Japan is lost. Expected /teas/c/green?origin=japan with 170 teas.

- [ ] **Some photo credits are still raw metadata** (tea-base; round 1 minor not fully fixed)
  - Page: /teas/{slug} photo credit
  - Viewport: both
  - What to do: Regex "No machine-readable author provided. X assumed..." down to X, and turn URL authors into a label. Decide how to show "author unknown".
  - Steps: Read captions on /teas/genmaicha, /teas/xihu-longjing ("Photo: http://www.chah.co.uk"), /teas/nilgiri-oolong, the Lapsang Souchong gallery and Bancha. "author unknown" is on Shui Jin Gui, Wuyi Shui Xian and Huang Guan Yin.

- [ ] **A few photos remain off: Chamomile shows a silver teapot, and 10 images are shared between teas** (tea-base)
  - Page: /teas/chamomile, /teas/kangra-black, /teas/kangra-green, /teas/sencha, /teas/shincha, /teas/fukamushi-sencha, /teas/kagoshima-sencha, /teas/nilgiri-oolong, /teas/nilgiri-frost-tea
  - Viewport: both
  - What to do: Swap the chamomile primary to a flower photo, and keep each shared photo on one tea.
  - Steps: Open /teas/chamomile. Compare main photos and galleries of Sencha and Shincha, Kangra Black and Green, Fukamushi and Kagoshima Sencha, Nilgiri Frost Tea and Nilgiri Oolong.

- [ ] **On the phone the tea's name is below the first screen** (tea-base)
  - Page: /teas/{slug} with a photo
  - Viewport: phone
  - What to do: Limit the phone photo height to about 40vh, or put the eyebrow and H1 above the gallery.
  - Steps: Open /teas/da-hong-pao without scrolling. The H1 is behind the tab bar at about y=590.

- [ ] **Account menu stays open after clicking or tapping outside it** (shell-feed)
  - Page: header account menu, all signed-in pages
  - Viewport: both
  - What to do: Add a document click listener that removes `open` from `.account-menu` when the click lands outside it.
  - Steps: Sign in as demo_user and open the menu. Click (400,500) on desktop or tap (30,500) on the phone. `details.account-menu.open` stays true, and on the phone the panel keeps covering the tabs and the first card.

- [ ] **Flash box and footer rule stick out 24px past the content column** (shell-feed)
  - Page: any page with a flash message; footer bottom rule
  - Viewport: desktop
  - What to do: Include the 24px side padding in the `.flash` width, and apply the same fix to the footer-bottom border. Also check the /login flash, which sits in a dark band above the split layout and pushes the page down.
  - Steps: At 1440, open /dev/login/demo_user or log out, then measure. The flash and footer rule run x=100-1340; the content runs x=124-1316.

- [ ] **Session card cup and avatar do not use the canvas sizes** (shell-feed)
  - Page: / session cards
  - Viewport: both
  - What to do: `macros.html` line 41 hard-codes the cup at 72. Use 76 on desktop, CSS-size it to 60 at phone widths, and set the desktop avatar to 40.
  - Steps: Open /?tab=sessions on both viewports and measure `.session-card .cup` and `.avatar`. The phone cup is 72-75px and the desktop avatar 36px. Canvas: Feed, MFeed.

- [ ] **Brew and source errors are never shown, so Save card seems to do nothing** (admin-security)
  - Page: /admin/teas/new, /admin/teas/{slug}/edit
  - Viewport: both
  - What to do: Render errors for `brew_*` and `sources` in `teas/_form_fields.html`, or add a summary at the top of the form.
  - Steps: As karyna, fill a valid tea, set Temperature 300, Steep seconds "abc", Sources "ftp://x", and save. The form reloads at the top with no message.

- [ ] **Admins can't add tea photos, and the page tells them to run a script** (admin-security; round 1 not fixed)
  - Page: /admin/teas/{slug}/edit
  - Viewport: both
  - What to do: Add a way to upload a photo, or write copy that makes sense to the owner.
  - Steps: Open the edit page of a tea with no photos. It says "No photos" and "Add them to the card file and re-run scripts/import_teas", and has 0 file inputs.

- [ ] **A user search with no results shows nothing** (admin-security; round 1 half fixed)
  - Page: /admin/users?q=zzz
  - Viewport: both
  - What to do: Add an empty state with a clear link, as /admin/teas has.
  - Steps: Search for zzz. Only "Users · 0" and the search box appear.

- [ ] **Empty drafts filter says: No teas match “”** (admin-security)
  - Page: /admin/teas?drafts=1
  - Viewport: both
  - What to do: Show "No drafts" and a link back, and remove the stray link underline on the drafts stat.
  - Steps: With 0 drafts, click the drafts stat on /admin/teas.

- [ ] **First stat is cut off: 2513 reads as 251** (admin-security)
  - Page: /admin/teas
  - Viewport: phone
  - What to do: Make all four digits fit. `.stat-value` has scrollWidth 85 against clientWidth 75.
  - Steps: Open /admin/teas on the phone.

## Polish

- [ ] **Phone header is taller than the canvas and uses a text pill for Log in** (shell-feed)
  - Page: header
  - Viewport: phone
  - What to do: Match MFeed: 56px header, 28px logo, round icon-only Log in.
  - Steps: Open / signed out. It measures 69px, a 32px logo and an 82px pill.

- [ ] **Following tab is cut off on narrow phones with no scroll hint** (shell-feed)
  - Page: / feed tabs
  - Viewport: phone at 320-360 wide
  - What to do: Fit all tabs, or clearly signal that the control scrolls.
  - Steps: Sign in and open / at 360 (scrollWidth 339, clientWidth 326) and 320 (339, 286), where the label reads "Foll". It fits at 375 and up.

- [ ] **Small differences from Feed and MFeed** (shell-feed)
  - Page: / Show more, phone pillar cards, phone footer
  - Viewport: both
  - What to do: Make Show more 50px and add the phone pillar chevrons. Ask the owner about the phone footer: MFeed draws none, but it carries Privacy and Terms.
  - Steps: Compare with the canvas: Show more is 44px, phone pillars have no chevron, and the phone shows the full footer.

- [ ] **Arrow keys do not work in the tea picker** (diary)
  - Page: /sessions/new
  - Viewport: desktop
  - What to do: Support ArrowDown and Enter to pick the highlighted tea.
  - Steps: Type sencha, press ArrowDown, press Enter. Nothing is picked.

- [ ] **A blank comment or empty steep fails silently** (diary)
  - Page: /sessions/{id}, and session save
  - Viewport: both
  - What to do: Show a short message.
  - Steps: Post a comment of only spaces; the page jumps to #comments. Separately, clear a steep's seconds and save; the steep disappears.

- [ ] **Small form details on the phone** (diary)
  - Page: /sessions/new
  - Viewport: phone
  - What to do: Centre the date text, move the Privacy legend off the card's top border, and remove the native number spinner and the empty space under Remove.
  - Steps: Scroll the page and open a steep. See `diary/p_low.png`, `diary/p_steep.png`.

- [ ] **Liquor edge of the cup looks uneven in WebKit** (diary)
  - Page: /sessions/{id}
  - Viewport: phone
  - What to do: Check on a real iPhone, and make it a clean circle if it shows there.
  - Steps: Look at the large cup while the swirl rotates; the lower right edge is flattened. See `diary/crop_cup.png`.

- [ ] **A session with almost no data looks empty** (diary)
  - Page: /sessions/{id}, story.png
  - Viewport: both
  - What to do: Design a deliberate minimal state.
  - Steps: Save a public session with only a tea name. The kicker reads "TEA SESSION · 0 STEEPS", and the story has about 500px of empty space. See `diary/story_min.png`.

- [ ] **Share button loses its icon and stays on "Link copied"** (forum)
  - Page: /discussions/{id}
  - Viewport: desktop
  - What to do: `copy()` in `static/app.js` sets textContent. Restore the original markup after about 2 s.
  - Steps: Click Share on the opening post and wait 4.5 s.

- [ ] **Header Reply button scrolls to the box but does not focus it** (forum)
  - Page: /discussions/{id}
  - Viewport: both
  - What to do: Handle the `.f-reply-btn` click in forum.js with `goToBox()`, or add an empty `data-reply-to`.
  - Steps: Click the gradient Reply button and read `document.activeElement`.

- [ ] **Rating wraps with the star and number on separate lines** (forum)
  - Page: /discussions/{id} attached session card
  - Viewport: phone
  - What to do: Wrap the rating in a nowrap span.
  - Steps: Open a topic with a session like 5 g · 150 ml · 70 °C · kyusu · no rinse · ★ 5.

- [ ] **Topic list uses Show more instead of the canvas page numbers** (forum)
  - Page: /discussions
  - Viewport: both
  - What to do: Keep it and record the difference, or add page numbers. It works and fits the rest of the site.
  - Steps: Create more than 20 topics and scroll to the end: "Show more" with "20 of 25".

- [ ] **Admin Hide button is lowercase** (forum)
  - Page: /discussions/{id}
  - Viewport: both
  - What to do: Capitalise it.
  - Steps: As karyna, read a reply's action row.

- [ ] **Pressing play makes the player outgrow its figure and shifts the page** (blog)
  - Page: /blog/{slug} with a TikTok video
  - Viewport: desktop
  - What to do: Fit the player to the figure's frame.
  - Steps: At 1440 press play. The 300x532 figure gets a 338x602 iframe, which sticks out 19px, and the heading moves about 36px. See `blog/d_video0.png`, `blog/d_video1.png`.

- [ ] **Comment tools mix casing and styles** (blog)
  - Page: /blog/{slug} comments
  - Viewport: both
  - What to do: Use one label style.
  - Steps: As karyna: Helpful is a pill, delete and hide are lowercase pink links, and Report is a capitalised muted link that wraps on the phone.

- [ ] **Small Markdown styling flaws** (blog)
  - Page: /blog/{slug}
  - Viewport: both
  - What to do: Remove the second pill behind fenced code and the blockquote's bottom space. Make wide tables scroll on the phone rather than break mid-word.
  - Steps: Publish a post with a blockquote, fenced code and a 6-column table, and view at 1440 and 390. See `blog/d_md1.png`, `blog/m_md.png`.

- [ ] **Rough spots in seeded guide text** (blog)
  - Page: storing-tea-at-home, first-gongfu-session, reading-puerh-wrapper
  - Viewport: n/a
  - What to do: Remove the doubled Fu brick phrasing, reconcile 100 ml in the text with 110 ml on the card, and reconsider the brewing recipe that ends the wrapper guide.
  - Steps: Read the three guides.

- [ ] **Ask banner shows a double full stop** (people)
  - Page: /discussions/new?ask=olena_tea
  - Viewport: both
  - What to do: Strip a trailing period from the display name in that sentence.
  - Steps: Signed in, on /u/olena_tea press Ask Olena in the forum: "Asking Olena K.. Keep..."

- [ ] **Follow shares the posting quota and ends on an error page** (people)
  - Page: /u/{handle}/follow
  - Viewport: n/a
  - What to do: Give follows their own limiter, or redirect back with a flash message.
  - Steps: Log 20 sessions in 10 minutes, then press Follow: 429 and no follow. That 20 follows would block posting was inferred from code, not reproduced.

- [ ] **Tea profile says "From 20 public sessions" above "No tea profile yet"** (people)
  - Page: /u/{handle}?tab=tea
  - Viewport: both
  - What to do: Hide the count line in the empty state, or explain that tea types come from teas picked from the base.
  - Steps: Log public sessions with free-text names and no tags, then open the tab.

- [ ] **Small visual leftovers in filters and headings** (tea-base)
  - Page: /teas, /teas/o/{key}, /search, /teas/types, /teas/a-z
  - Viewport: phone
  - What to do: Shorten the sort option to "Most known", style the native selects, add a gap to `.origin-regions`, remove link styling from the headings, and tidy the Search button's separate row.
  - Steps: Open /teas, /teas/o/japan and /search?q=oolong on the phone.

- [ ] **Search labels articles as teas, and popularity outranks name match** (tea-base)
  - Page: /search?q=sencha, /search?q=jasmine
  - Viewport: both
  - What to do: Rank names containing the query above aliases, and use a Guide label for `style-*` cards, whose pages also still have a tea eyebrow, Brewing card and How it is sold.
  - Steps: For sencha, Uji Matcha is second. For jasmine, "Scented Teas" appears as TEA · GREEN. Open /teas/style-aged-teas.

- [ ] **Hub cards in the second row don't line up** (admin-security)
  - Page: /admin
  - Viewport: desktop
  - What to do: Align the titles, and stop Sessions looking like a link when it is not one.
  - Steps: At 1440, titles sit at y≈358, 346 and 334.

- [ ] **Tea counts disagree between hub and list** (admin-security)
  - Page: /admin, /admin/teas
  - Viewport: both
  - What to do: Use the same number, or label the 10 explainer cards.
  - Steps: The hub says 2523 cards; the list says 2513 published and 0 drafts.

- [ ] **List rows change layout with name length** (admin-security; round 1 not fixed)
  - Page: /admin/users, /admin/teas
  - Viewport: phone
  - What to do: Use the same row layout for every entry.
  - Steps: Open /admin/users. Compare demo_user and tea_mike.

## Questions for the owner

Testers raised these in their verdicts. They are not counted as bugs.

- [ ] **Share format:** Share opens a bare PNG in a new tab rather than a phone share sheet. The diary tester finds this clumsy for TikTok and Instagram.
- [ ] **@prolyv profile:** it opens as an empty profile with all zeros.
- [ ] **Start for free card:** it shows only to signed-out visitors, while the canvas has no such condition. The people tester would keep it, but it is not in the decisions list.
- [ ] **Catalogue default:** consider showing only documented teas by default. 1733 of 2523 cards are low confidence, and 37 of the 100 best-known teas have no photo, including Bi Luo Chun, Pu-erh and Masala Chai.
- [ ] **Forum view counts:** the forum tester thinks leaving them out is fine, rather than showing a number that isn't real.
- [ ] **In this series and the email card:** the blog tester thinks dropping them is fine for launch, but together they leave the desktop page without its right column.

## Not tested in this round

- **Real devices:** real iPhone Safari, including blur, native pickers and share sheet, select rendering, a real double tap on Save, and the timer on screen lock. Sharing from a phone into TikTok or Instagram. Tablet widths on the diary.
- **Real services:** Telegram and Google sign-in or linking, SMTP delivery, a real Instagram reel, the Telegram widget popup, and removing a real avatar photo.
- **Production:** Caddy and deployment, whether `MemoryMax` kills the worker on the VM, and behaviour with `SOCIAL_*` and `CONTACT_EMAIL` set (the shell-feed tester saw socials and Contact render on a private instance; hero pills and privacy copy remain unchecked).
- **Paging, for lack of data:** diary Show more (30 sessions), session comments over 20, blog comments at 10, brew-along over 12, followers and notifications over 30.
- **Forum:** "Same tea, N ways", compare via the forum button, replies on a hidden topic, notifications about a deleted topic, direct POST marking your own reply, the 20000-character cut-off, Show more without JavaScript, session cards with photos, and Follow or Save after a real login.
- **Other:** feed Back after Show more with back-forward cache, the in-app notice's Copy link, /settings Avatar wording, the compare page's session chooser, the 404 page signed in, whether a deleted handle can be re-registered, average rating and top-note line on blog, Open Graph previews, external tea links opened (hrefs read only), and every one of the 2523 cards (sampled).

## Test data left on the 8100 database

- **Accounts:** qa2sh856310, qa2_people7 and deleted accounts derived from qa2_people2.
- **Sessions:** DblReal, DoubleTap, BigPhoto, Phone24, Phone48, Photos5, BadPhoto, QuotaProbe, EmptySec, WWW..., and a long-W topic and session.
- **Content:** post qa2-cover-post-56245, a public Da Hong Pao by olena_tea (b8b958b2-82e4-4afe-850c-062b59826d5e) with comments and one report, QA comments on two guides, a QA forum thread, brew-along sessions, and a follow of olena_tea.
