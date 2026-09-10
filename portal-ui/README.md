# portal-ui

The React SPA behind `dashboards.tools.stratevi.com` — the tool menu every
signed-in user sees, plus the admin control plane. Static bundle only: it is
built to `dist/`, copied into the proxy image, and served by `proxy-app/`
from the same origin as the JSON API it talks to.

Stack: Vite 5 + React 18 + TypeScript + Tailwind 3. No server-side anything.

- Contract: `docs/design/portal-api.md` — the UI is written against that file
  and nothing else. `src/api/types.ts` is a hand transcription of it; if the
  contract moves, that file moves in the same commit.
- Product context: `docs/design/portal.md` (P1 = menu + read-only-ish admin).
- Branding: Stratevi. Plain and professional, matching the proxy's own
  operational pages (`proxy-app/proxy_app/page.html`) — **not** Assembled
  Intelligence.
- UX patterns (not visuals) are ported from assembled.work's app portal: see
  "Design language" below for what was taken and what was left.

## Commands

```powershell
cd portal-ui
npm install          # once; package-lock.json is committed

npm run dev          # http://localhost:5173, /api proxied to localhost:8080
npm run dev:mock     # same, but no backend needed at all (see below)
npm run build        # tsc -b && vite build  ->  dist/
npm test             # vitest, single run
npm run test:watch
npm run typecheck
```

`npm run dev` expects `proxy-app` to be listening on `http://localhost:8080`;
the Vite proxy in `vite.config.ts` forwards `/api/*` there. Nothing else is
proxied, because nothing else needs to be — the SPA owns every non-API path.

## Mock mode

`npm run dev:mock` sets `VITE_PORTAL_MOCK=1` (see `.env.mock`). The API client
then routes every call through `src/api/mock.ts`, which serves the fixtures in
`src/api/fixtures.ts` as real `Response` objects — so CSRF, JSON parsing and
error mapping all run exactly as they do against the live service. It covers
all five live states, expiry edge cases, a reserved-mode app, and 137 audit
events per host so "load more" is exercised.

Two tricks while in mock mode:

```js
__portalMock.setAdmin(false)   // then reload: the non-admin view + 403 state
localStorage.removeItem('portalMockAdmin')   // back to admin
```

`VITE_PORTAL_MOCK` is statically false in a normal build, so Vite drops the
branch and neither the mock transport nor the fixtures reach `dist/`.

## Where `dist/` goes

`npm run build` writes:

```
dist/
  index.html                     entry; also the SPA fallback for every
                                 non-API path on a portal host
  assets/index-<hash>.js         ~219 kB (69 kB gzipped)
  assets/index-<hash>.css        ~24 kB (5.3 kB gzipped)
```

Filenames are content-hashed; only `index.html` has a stable name, and it
references the hashed assets. Everything is requested from the site root
(`/assets/...`), so the bundle must be served at `/`, not under a prefix.

The deploy (someone else's wiring, documented here so it isn't a surprise):
the proxy's Docker build gains a Node stage that runs this package's `build`
and copies `dist/` to **`/app/portal-dist`** in the runtime image. The Python
runtime image stays Node-free. `proxy-app` then serves, on portal hosts only:

| Request | Served from |
|---|---|
| `/api/v1/*` | JSON handlers |
| `/assets/*` | `/app/portal-dist/assets/*` |
| anything else | `/app/portal-dist/index.html` (client-side routing) |

`/__proxy/*` stays reserved everywhere, and app hosts are untouched.

Client-side routes the fallback has to cover: `/`, `/admin`,
`/admin/apps/:host`. Anything else redirects to `/` in the browser.

## Layout

```
src/
  api/
    types.ts          hand-written from portal-api.md
    client.ts         fetch wrapper: CSRF header, ApiError, {error} mapping
    fixtures.ts       the only fake data in the repo
    mock.ts           mock transport (dev-only, lazy-imported)
    client.test.ts
  components/
    Layout.tsx        header, nav (Admin hidden for non-admins), footer
    Button.tsx        primary / outline / ghost / danger, as button or link
    StatusBadge.tsx   awake/starting/asleep/disabled/expired — one place
    Monogram.tsx      deterministic two-letter card art  (+ test)
    StatTile.tsx      a metric that is a door: click it, the list filters
    Tabs.tsx          underlined tab strip with real tablist ARIA
    icons.tsx         ~16 hand-drawn 24-grid glyphs; no icon dependency
    EmailTagEditor.tsx  allowed_emails editor  (+ test)
    ExpiryPicker.tsx    expires_at with a never toggle  (+ test)
    states.tsx        PageHeader / SectionCard / Panel / Notice /
                      Loading / Empty / Error / NoAdminAccess
  hooks/
    useResource.ts       load once, reload on demand, abort on unmount
    useVisiblePolling.ts 15s polling that stops when the tab is hidden
    useAppCollection.ts  admin browse block: search, filter, sort,
                         remembered card/table view  (+ test)
  lib/
    time.ts           epoch <-> local date, relative time
    appDisplay.ts     the short human phrases the cards are made of:
                      access summary, expiry tense, wake hint, greeting,
                      attention flags  (+ test)
    meContext.ts
  pages/
    MenuPage.tsx           /            card grid  (+ test)
    AdminAppsPage.tsx      /admin       stats + browse block + table/cards
                                        (+ test)
    AdminAppDetailPage.tsx /admin/apps/:host  header, tabs, glance rail
    AppSettingsForm.tsx    the PATCH form, in sections
    AuditLog.tsx           cursor-paginated event list
  test/
    setup.ts
    render.tsx        renderPage(): a page under router + MeContext
```

## Design language

Light, one accent (`#3b6ce4`, the same blue the proxy's own pages use), a
system font stack, 12 px card radius, 8 px control radius. Everything is
bundled: **no webfont, no icon package, no component library** — the CSP the
proxy will eventually serve is unknown, so nothing in the page may reach out
to a CDN.

Colour carries exactly one meaning. Emerald / amber / red are the status
language (awake, starting-or-attention, expired). Card art therefore uses
cool tints only (slate, sky, indigo, violet, cyan, zinc) — a decorative tile
must never be mistakable for a status, and there is a test asserting it.

Ported from assembled.work, in patterns rather than pixels:

| Their thing | Ours |
|---|---|
| Dashboard greeting + one-sentence "pulse" | Menu page header, counting awake tools |
| `PreviewCard` art + overlaid status chip | `Monogram` band + `StatusBadge` (we have no screenshots to show) |
| `StatTile` — "every number is a door" | Admin stats; clicking one filters the list |
| `useAppCollection` browse block | Admin search / filter / sort / remembered view |
| `Show` header: face, name, chip, meta line, Open + Copy | Detail page header |
| `Show` tabs + "At a glance" rail | Detail page tabs + rail |
| Two-column definition layout in settings | `Field` in `AppSettingsForm` |
| `EmptyState` with a calm graphic and a way out | `EmptyState` |

Deliberately **not** ported: their palette, logos and dark mode (the portal is
light-only and Stratevi-branded); teams / organizations / invitations (no
second org, and the reserved access modes stay greyed out); releases, version
history, rollback and the upload drop zone (P2 — see `docs/design/portal.md`);
the onboarding tour; the browse block on the *menu* page, because a client
with three tiles does not need to filter three tiles.

The "+ New app" affordance is present for admins and deliberately inert
(disabled, tooltipped "coming in P2") on both the menu and the admin list. It
sets the expectation without inventing a route that 404s.

## Behaviour worth knowing

- **Auth**: none in the UI. The ALB's Cognito session is already established
  before any request reaches the origin. `GET /api/v1/me` is fetched once at
  boot; if it fails the app renders a single error card, because nothing
  below it can be trusted.
- **Admin gate**: the nav item and the routes are hidden when `is_admin` is
  false, and a 403 from anything under `/api/v1/apps` renders the same
  "you don't have admin access" card. The API is the real enforcement.
- **CSRF**: `X-Portal-Csrf: 1` is attached to every non-GET/HEAD/OPTIONS
  request by the client, so no call site can forget it.
- **Polling**: the menu and the admin list re-fetch every 15 s while the tab
  is visible, silently (no spinner). A hidden tab polls not at all and fires
  one catch-up fetch when it comes back. No websockets in P1.
- **PATCH is a diff**: only fields the admin actually changed are sent, which
  keeps the audit event's `path` (the changed field names) honest.
- **Disabling asks first.** Nothing else does.
- **The wake cost is stated, not hidden.** A sleeping tile says "Starts in
  ~30–60s when opened", because a scale-to-zero app that looks instant and
  then isn't reads as broken.
- **The card/table choice sticks** in `localStorage` (`portalAdminView`), and
  falls back to the table if storage throws.

## Contract notes for the backend

Things `docs/design/portal-api.md` leaves open. The UI copes with both
readings in each case; pinning them down in the doc would be better.

1. **`GET /api/v1/apps` envelope.** The doc says "Array of full app objects"
   but every other collection is wrapped (`{"apps": [...]}`). The client
   accepts either. Please pick one and write it down.
2. **`expires_at` time-of-day.** The contract gives epoch seconds but no
   convention for what a calendar date means. The picker writes **23:59:59
   local time on the chosen day**, so "expires 30 Sep" leaves the app usable
   all of the 30th. If the reaper assumes start-of-day, dates will look a day
   short.
3. **`last_active` / `awake_since` nullability.** The example shows
   `"last_active": 1789000000` with no null case, but a never-opened app must
   have one. The UI treats both as `number | null` and renders "never".
4. **Reviving an expired app.** `status` may only be set to `active` or
   `disabled`, and expiry "is the reaper's job". So the only way back from
   `expired` is presumably PATCHing a future `expires_at` — the UI says so in
   a hint, but whether the row's `status` then returns to `active` on its own
   is not specified.
5. **Empty `allowed_emails` with `access_mode: users`.** `seed.py` refuses
   this; the contract does not say whether PATCH does. The UI warns rather
   than blocks, so if the API rejects it the admin sees a 400 they were
   already warned about.
6. **Audit `limit` ceiling.** Unspecified; the UI asks for 50 a page.
7. **Audit `event` vocabulary.** Only `allow` and `config_change` are named.
   `StatusBadge` and the audit table both fall back to a neutral chip with
   the raw string, so new event names and the P2 `building` /
   `build_failed` live states will not break anything.
8. **`GET /api/v1/me` for a signed-in user with no entitlements.** Assumed to
   be a 200 with `is_admin: false`; the menu then renders its empty state.
