# Design prototype

An export of the hi-fi design canvas the site was designed from. The live, clickable version is at
https://claude.ai/artifact/C8hsrKfz3Upiouz3tPi1WN

Each `*.dc.html` file is one screen in the canvas's Design Component format. These files do not run on their own in a
browser: they need the canvas runtime (`support.js`). Read them as reference markup and copy.
`canvas.json` is the index, holding screen sizes, pages and layout.

| Canvas page | Screens |
|---|---|
| Phone · diary, forum, blog | `MFeed`, `MSessionPost`, `MForum`, `MTopic`, `MBlog`, `MBlogPost`, `MProfile`, `MDiary`, `MNewSession`, `MLogin`, `MobileNav` |
| Diary · forum · blog (desktop) | `Feed`, `SessionPost`, `Profile`, `Forum`, `Topic`, `Blog`, `BlogPost`, `MyDiary`, `NewSession`, `Login`, plus the `Header`, `Footer`, `Cup`, `Story` and `Splash` components |
| Hero · 5 variations | `HeroA` through `HeroE` |
| Later: catalog, shops, events | `Home`, `Catalog`, `TeaPage`, `Shop`, `Events`, `Club`, `ForShops` |
| Draft v1 | `Main`, `Tea`, `Diary`, `QuickLog`, `ShareCard` |
