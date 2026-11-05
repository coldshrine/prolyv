# Prolyv

Tea diary, forum and blog — the home of the Prolyv TikTok and Instagram. Short videos live there; full brewing
breakdowns, discussion and personal diaries live here.

Milestones so far:

1. **Login and accounts** — Google / Telegram sign-in, sessions, settings, public profiles.
2. **Tea base** — an encyclopedia of tea cards with photos from Wikimedia Commons, search and filters, admin editing,
   sitemap and structured data.
3. **Diary** — tea sessions with a steep timer, private by default, comments, "brew it the same way".
4. **Forum and feed** — sections, topics with attached sessions, helpful replies, full-text search, home feed.
5. **Blog** — Markdown posts with TikTok / Instagram embeds, brew-along sessions, comments.

## Stack

- Python 3.12, FastAPI, Jinja2 server-side templates, HTMX for the small interactive bits (no build step).
- SQLite in WAL mode through SQLAlchemy 2.x and Alembic. Moving to Postgres later is a `DATABASE_URL` change.
- No passwords. Sign in with Google (OpenID Connect) or the Telegram Login Widget.
- Deployed with uvicorn behind Caddy (automatic HTTPS) on the same Oracle always-free VM as the Dota analyzer.

## Run it locally

Requires [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 itself if needed).

```bash
uv sync
cp .env.example .env
```

Edit `.env`: set `SECRET_KEY` to the output of

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

and leave `BASE_URL=http://localhost:8000`. Google and Telegram credentials are optional locally: without them
the login page shows a "not configured" note instead of the button. Then:

```bash
uv run alembic upgrade head
```

```bash
uv run uvicorn prolyv.main:app --reload
```

Open <http://localhost:8000/login>. For a populated local site:

```bash
uv run python scripts/seed_prod.py
```

```bash
uv run python scripts/seed_demo.py
```

`seed_prod.py` runs `scripts/import_teas.py`, which fills the tea base from `data/teas/**/*.json`, fetches Wikipedia links
and copies the photos listed in the card files (slow the first time; responses are cached in `data/cache/`, photos in
`media/`), and then adds the starter topics and guides written under the Prolyv name. `seed_demo.py` adds demo users and activity, and
only runs on localhost. With `DEV_LOGIN=1` and `ADMIN_HANDLES=karyna` in `.env`, press "Continue as demo user" on `/login` or
open `/dev/login/<handle>` (`karyna` is the admin). Never set `DEV_LOGIN=1` on a server: the app refuses to start.

Tests:

```bash
uv run pytest
```

The suite covers Telegram hash verification (valid, tampered, stale), the Google code flow against a mocked
Google (state, nonce, audience, failed exchange), the session cookie lifecycle, handle generation and collisions,
settings validation and handle uniqueness, the unlink-last-provider refusal, soft delete, CSRF, rate limiting,
in-app browser detection and the Alembic migration matching the models.

## The tea base

Every tea is a JSON file under `data/teas/<group>/<slug>.json`; `data/teas/SCHEMA.md` documents the fields, the
enums and the house voice. The cards are the source of truth for the text; `scripts/import_teas.py` upserts them
into the `teas` table and enriches each one:

- the English Wikipedia article (`wikipedia_title`) gives the "Read more on Wikipedia" link and, for cards about
  that exact tea, the Wikidata id;
- photos come only from the `images` list in the card file, with a free licence (CC BY, CC BY-SA, CC0, public domain),
  and are shown with "Photo: author · licence" and a link to the source page. A card without the list has no photos, and
  a photo taken off a card is deleted from `media/teas/`. The old automatic search found unchecked pictures, so it runs
  only with `--autosearch-photos`, and never for a card that has the key, even empty;
- a card without a photo gets a cup placeholder in the colour of its category.

Wikimedia allows 200 requests a minute to a client with a contact in its User-Agent and at most 3 at once, and
Retry-After must be obeyed. The importer sends one request at a time at 180 a minute, so expect about one minute per
180 photos on the first run. It obeys Retry-After; asked to wait more than 15 minutes it finishes without the network and exits 1.

Stopping and re-running is safe. Each card is committed on its own, a bad card is reported and skipped, and a second run
makes no requests for what it already has. Cards edited by an admin in the UI are locked and keep their text, photos and
status. The search index rebuilds at the end in one transaction, about 15 seconds for 5000 cards, and writes on the live
site wait for it, so run big imports when the site is quiet. Options: `--group japan`, `--only sencha`, `--refresh` (ignore
the cache), `--dry-run` (validate only), `--no-network`, `--prune` (unpublish teas whose card file is gone).

Teas are added only by the team: the card files plus the importer, or the admin form at `/admin/teas/new`. Users do
not add teas. Admins (`ADMIN_EMAILS` or `ADMIN_TELEGRAM_IDS`, matched against a verified sign-in; `ADMIN_HANDLES` works on localhost only) can edit any card and manage its photos; blog posts are
written at `/admin/posts`.

## Что сделать руками, 5 минут

Сайт работает и без ключей: на localhost вход идёт по ссылке на e-mail (без SMTP ссылка показывается прямо на
странице) или кнопкой «Continue as demo user» (`DEV_LOGIN=1`, только при localhost). Настоящие ключи создаёте вы;
значения вводятся так, чтобы они нигде не отображались:

```bash
uv run python scripts/set_keys.py
```

Секреты живут только в `.env` локально и в `/etc/prolyv.env` на сервере. `.env` и `*.db` в git не попадают.
Пустые значения и значения с `placeholder` считаются «не настроено»: такая кнопка просто не показывается.

### Google

1. [Google Cloud Console](https://console.cloud.google.com/) → создать проект → **APIs & Services → OAuth consent
   screen**: User type **External**, название Prolyv, ваш e-mail, scopes `openid`, `email`, `profile`.
2. **Первым делом добавьте свой e-mail в Test users.** Пока статус Testing, войти могут только они; остальные получат
   `403 access_denied`.
3. **Credentials → Create credentials → OAuth client ID → Web application.** Authorized redirect URIs, ровно так:
   - локально: `http://localhost:8000/auth/google/callback`
   - прод: `https://<ваш-домен>/auth/google/callback`
4. Client ID и secret введите через `set_keys.py` и перезапустите приложение. На localhost Google заработает сразу.

Для статуса Production (вход для всех) Google требует домашнюю страницу, `/privacy` и `/terms` на подтверждённом домене.
Эти страницы на сайте есть. Голый IP Google не примет. Примет ли `*.sslip.io`, не подтверждено (есть только
противоречивые отчёты), поэтому не рассчитывайте на него. План Б: свой домен или Cloudflare Tunnel со своим доменом.

### Telegram

1. [@BotFather](https://t.me/BotFather) → `/newbot` → имя и username, заканчивающийся на `bot`. Получите токен.
2. `/setdomain` → выбрать бота → хост из `BASE_URL`, например `prolyv.example.com`.
3. Токен и username (без `@`) введите через `set_keys.py`.

BotFather не принимает голый localhost, поэтому на localhost кнопки Telegram нет, вместо неё честная строка. Как
проверить локально: алиас в `/etc/hosts` (`127.0.0.1 prolyv.test`) с `/setdomain prolyv.test` и `BASE_URL=http://prolyv.test:8000`,
либо туннель (Cloudflare Tunnel, ngrok) с его доменом в `/setdomain`. При обходе с алиасом задайте порт 80, если
виджет его потребует.

### Письма со ссылкой для входа (на проде)

Подойдёт любой SMTP-релей (Brevo, Postmark, Mailgun, пароль приложения Gmail): `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`,
`SMTP_PASSWORD`, `SMTP_FROM`. На проде без SMTP вход по e-mail скрыт.

## How sign-in works

- **Google.** `/auth/google/start` creates a random `state` and `nonce`, keeps them in a signed cookie that
  expires after 10 minutes, and redirects to Google. `/auth/google/callback` checks the `state` against the cookie,
  exchanges the code (Authlib), verifies the ID token signature against Google's JWKS and the `iss`, `aud`, `exp`,
  `iat` and `nonce` claims (joserfc). The e-mail is stored only when Google says it is verified.
- **Telegram.** The "Continue with Telegram" button is ours, styled like the design; it calls
  `Telegram.Login.auth` from Telegram's widget script. When the popup is blocked (in-app browsers) it falls back
  to a full-page sign-in and comes back through `#tgAuthResult`. The result is POSTed to
  `/auth/telegram/callback`, which also accepts the standard `data-auth-url` GET. The server recomputes the
  HMAC-SHA256 with `SHA256(bot_token)` as the key, rejects `auth_date` older than 24 hours, and requires a signed
  "intent" cookie that only our login and settings pages set (so a callback URL pasted into another browser
  cannot log that browser in).
- **Accounts.** First login creates the user and the identity. A logged-in user signing in with the other
  provider links it to the same account. A Google login whose verified e-mail matches an existing Google identity
  joins that account. The handle is a slug of the Telegram username or Google name (`[a-z0-9_]`, 3–24 characters,
  Cyrillic transliterated) with a numeric suffix on collision.
- **E-mail link.** `/auth/email/start` creates a one-time token (15 minutes, SHA-256 stored, at most 5 per address per
  hour) and sends it by SMTP. The link opens a confirmation page with a button, so mail scanners that fetch links do
  not burn it. A verified address joins the Google account with the same address. Without SMTP the link is shown on
  the page and logged, on localhost only; in production the option is hidden.
- **Sessions.** Cookie `prolyv_session` holds a random 256-bit token; the database stores its SHA-256 only.
  HttpOnly, SameSite=Lax, Secure whenever `BASE_URL` is https. 30-day expiry that slides on use (refreshed at most
  once an hour), rotated on every login, deleted on logout, purged on account deletion.
- **Forms.** Every POST carries a CSRF token bound to the session (HMAC of the session hash). `/login` and
  `/auth/*` are rate limited per client IP (30 requests a minute by default, in memory).
- **In-app browsers.** Google refuses to sign in inside Instagram, TikTok and Facebook web views
  (`disallowed_useragent`). `/login` detects those user agents, hides the Google button and shows "Open in your
  browser to use Google, or continue with Telegram" with a copy-link button.
- **Delete account** is a soft delete: the row stays (future posts can point at it), sessions and identities are
  removed, the handle is freed, name/bio/avatar are scrubbed. Signing in again creates a fresh account.

Rotating `SECRET_KEY` invalidates in-flight OAuth, flash and intent cookies and CSRF tokens, but not logins:
sessions are database rows, not signed cookies.

## Search

`/search` uses one SQLite FTS5 index (`search_fts`) over teas, topics with their replies, posts and public sessions, kept in
step by the routes and rebuilt by the importer. Chinese characters are indexed one by one, because the tokenizer doesn't
split them. The filter box on `/teas` uses `LIKE` over a normalised `search_text`, which is fine for thousands of rows.

## Project layout

```
prolyv/
  app.py            create_app(): middleware, routers, error pages
  main.py           uvicorn entry point (prolyv.main:app)
  config.py         Settings from the environment
  db.py             engine, WAL pragmas, per-request session
  models.py         users, identities, sessions
  security.py       tokens, hashes, signed cookies, CSRF
  handles.py        handle slugs and uniqueness
  ratelimit.py      in-memory limiter + middleware
  auth/
    google.py       OIDC client (Authlib + joserfc)
    telegram.py     Login Widget verification
    accounts.py     sign-in resolution, linking, unlinking, soft delete
    sessions.py     create / load / slide / revoke
  search.py         FTS5 site search
  social.py         notifications and follows
  uploads.py        session photos (re-encoded, metadata removed)
  story.py          1080x1920 story card PNG
  mail.py           SMTP for sign-in links
  markdown.py       Markdown -> sanitised HTML for the blog
  web/
    routes_auth.py     /login, /auth/google/*, /auth/telegram/callback, /logout
    routes_account.py  /settings, /settings/handle-check, /u/{handle}
    routes_teas.py     /teas, /teas/{slug}, /teas/c/{category}, /teas/o/{origin}, /teas/new, /admin/teas, sitemap.xml
    routes_diary.py    /diary, /sessions/new, /sessions/{id}, comments, /teas/suggest
    routes_forum.py    /discussions, /discussions/{id}, replies, votes, search
    routes_blog.py     /blog, /blog/{slug}, comments, /admin/posts
    routes_home.py     / (feed), /healthz, legacy redirects
    routes_social.py   follow, /notifications, /saved, /report, /admin, /admin/reports, /admin/users
    routes_pages.py    /about, /rules, /privacy, /terms, robots.txt, manifest, icons
    routes_dev.py      /dev/login (DEV_LOGIN=1 on localhost only)
    deps.py, cookies.py, flash.py, templating.py, useragent.py
  templates/        base (top bar + bottom tab bar), login, settings, profile, teas/, diary/, forum/, blog/, admin/
  fonts/            Unbounded and Manrope (SIL OFL) for story cards
  static/           app.css (design tokens), app.js, session.js (steep timer), telegram.js, icons, htmx 2.0.4 (vendored)
data/teas/          tea cards (JSON) + SCHEMA.md; data/cache/ is the importer's HTTP cache (git-ignored)
scripts/            import_teas.py, seed_prod.py, seed_demo.py, set_keys.py
alembic/            migrations (alembic upgrade head)
tests/              pytest suite (uv run pytest)
deploy/             prolyv.service, Caddyfile, backup.sh
```

## Deploy to the Oracle VM

Same machine and pattern as the Dota analyzer: `ubuntu@144.24.168.1`, always-free `VM.Standard.E2.1.Micro`
(1 OCPU, 956 MB RAM, 2 GB swap), Ubuntu 22.04, rsync the code up, run it under a supervisor, Caddy terminates
HTTPS. Nothing new is created in Oracle Cloud, so the cost stays at $0. The micro instance's sshd drops
connections now and then — retry `ssh` two to four times.

Google may refuse a `*.sslip.io` redirect URI when the app goes to Production, and that isn't confirmed either way. Telegram and
e-mail links don't depend on it. If Google refuses it, use your own domain or a Cloudflare Tunnel with your own domain.

The one difference: the Dota analyzer's Caddy runs **inside Docker** and already owns ports 80 and 443, so Prolyv
is a site block in that Caddy, not a second Caddy. Prolyv itself runs as a systemd service on the host.

```bash
export SSH="ssh -i ~/Downloads/ssh-key-2026-06-25.key"
```

1. **Tools on the VM (once).**

   ```bash
   $SSH ubuntu@144.24.168.1 'curl -LsSf https://astral.sh/uv/install.sh | sh && sudo apt-get install -y sqlite3 && sudo mkdir -p /var/lib/prolyv/backups && sudo chown -R ubuntu:ubuntu /var/lib/prolyv'
   ```

2. **Copy the code** (never `.env`, never the database):

   ```bash
   rsync -az -e "$SSH" --exclude .venv --exclude .git --exclude '.env' --exclude '*.db*' --exclude __pycache__ ./ ubuntu@144.24.168.1:prolyv/
   ```

3. **Install dependencies** (uv fetches Python 3.12 on first run):

   ```bash
   $SSH ubuntu@144.24.168.1 'cd prolyv && ~/.local/bin/uv sync --no-dev'
   ```

4. **Secrets.** Create `/etc/prolyv.env` from `.env.example` and fill it in. Values that differ from local:
   `BASE_URL=https://prolyv.144.24.168.1.sslip.io`, `DATABASE_URL=sqlite:////var/lib/prolyv/prolyv.db`,
   `MEDIA_DIR=/var/lib/prolyv/media`, `ADMIN_EMAILS=<the e-mail you sign in with>` (or `ADMIN_TELEGRAM_IDS=<your numeric Telegram id>`), no `DEV_LOGIN` (the app won't start with it),
   `BIND_HOST=0.0.0.0` (so the Caddy container can reach uvicorn through the host gateway; port 8010 is closed
   in the OCI security list and in the host firewall, so nothing else can).

   ```bash
   $SSH ubuntu@144.24.168.1 'sudo cp prolyv/.env.example /etc/prolyv.env && sudo chown root:ubuntu /etc/prolyv.env && sudo chmod 640 /etc/prolyv.env && sudo nano /etc/prolyv.env'
   ```

5. **Database.**

   ```bash
   $SSH ubuntu@144.24.168.1 'cd prolyv && set -a && . /etc/prolyv.env && set +a && .venv/bin/alembic upgrade head'
   ```

6. **Service.**

   ```bash
   $SSH ubuntu@144.24.168.1 'sudo cp prolyv/deploy/prolyv.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now prolyv && sleep 2 && curl -s localhost:8010/healthz'
   ```

   Expect `{"ok":true}`. Logs: `journalctl -u prolyv -f`.

7. **Caddy (the Dota analyzer's container).** In `~/dota-tournament-analyzer/deploy/`:
   - append the site block from `deploy/Caddyfile` to its `Caddyfile`, with `reverse_proxy host.docker.internal:8010`;
   - in `docker-compose.yml`, under the `caddy` service, add `extra_hosts: ["host.docker.internal:host-gateway"]`;
   - `sudo docker compose up -d` (Caddy reloads, obtains the certificate for the new host automatically).

   Check <https://prolyv.144.24.168.1.sslip.io/healthz>, then `/login`. When you move to a real domain, point its
   DNS A record at the VM, change the site address in the Caddyfile and `BASE_URL`, update the Google redirect
   URI and BotFather's `/setdomain`.

8. **Backups.** Nightly `sqlite3 .backup` with 14 days kept:

   ```bash
   $SSH ubuntu@144.24.168.1 '(crontab -l 2>/dev/null; echo "0 3 * * * /home/ubuntu/prolyv/deploy/backup.sh >> /var/lib/prolyv/backups/backup.log 2>&1") | crontab -'
   ```

   Run `deploy/backup.sh` once by hand to see a `prolyv-<stamp>.db.gz` appear in `/var/lib/prolyv/backups`.

**Updating later:** repeat steps 2, 3 and 5, then `sudo systemctl restart prolyv`.

**Tea base on the server:** after the first deploy run the importer once on the VM (it only needs outbound
HTTPS): `cd ~/prolyv && set -a && . /etc/prolyv.env && set +a && .venv/bin/python scripts/import_teas.py`.
Re-run it whenever cards change. Use `scripts/seed_prod.py` for the first run, which also adds the starter topics and
guides. `scripts/seed_demo.py` refuses to run on a real domain.

**Photos on the server:** set `MEDIA_DIR=/var/lib/prolyv/media`. Only `media/teas/` is served as static files. Session photos go through the app, which shows those of private or hidden sessions to their author and admins only, so never point Caddy straight at this folder. Include that folder in your backups, because session photos
live only there; tea photos can be regenerated by the importer.

**Memory.** One uvicorn worker (~70 MB) next to the Dota containers is fine on 956 MB plus swap; the unit caps
Prolyv at 300 MB and restarts it if it ever grows past that.

## What is still a stub

- Avatar upload (the avatar comes from the sign-in provider) and Open Graph images for sessions.
- Search on Postgres (FTS5 is SQLite-only; `prolyv/search.py` is the one module to change).
- E-mail notifications: notifications are in-app only.
