# portal-ui

The React SPA behind `dashboards.tools.stratevi.com` — the tool menu every
signed-in user sees, plus the admin control plane. Static bundle only: it is
built to `dist/`, copied into the proxy image, and served by `proxy-app/`
from the same origin as the JSON API it talks to.

Stack: Vite 5 + React 18 + TypeScript + Tailwind 3. No server-side anything.

- Contract: `docs/design/portal-api.md` — the UI is written against that file
  and nothing else. `src/api/types.ts` is a hand transcription of it; if the
  contract moves, that file moves in the same commit.
- Product context: `docs/design/portal.md` (P1 = menu + read-only-ish admin),
  `docs/design/portal-p2a.md` (P2a = self-service creation: the wizard and the
  build screen).
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
all seven live states, expiry edge cases, a reserved-mode app, and 137 audit
events per host so "load more" is exercised.

**The whole creation wizard runs in mock mode with no backend**, including the
upload: `uploadToS3` swaps its XHR for a timer that fires the same progress
callbacks, so the progress bar is exercised for real without a bucket. The
mock's `validate-key` implements the same order of checks as the spec (shape →
reserved → denylist → collision) and its denylist refusal never names the term
that matched. `smoke-fail`, or any key containing `fail`, builds and then
fails, so that screen is reachable too.

Tricks while in mock mode:

```js
__portalMock.setAdmin(false)      // then reload: the non-admin view + 403 state
__portalMock.setCreator(false)    // an admin who may NOT create: no "+" anywhere
__portalMock.failNextBuild()      // the next created app's build fails
localStorage.removeItem('portalMockAdmin')     // back to admin
localStorage.removeItem('portalMockCreator')   // back to creator
```

`src/api/mock.test.ts` smoke-tests those routes, because a mock that lies is
worse than no mock.

`VITE_PORTAL_MOCK` is statically false in a normal build, so Vite drops the
branch and neither the mock transport nor the fixtures reach `dist/`.

## Where `dist/` goes

`npm run build` writes:

```
dist/
  index.html                     entry; also the SPA fallback for every
                                 non-API path on a portal host
  assets/index-<hash>.js         ~264 kB (83 kB gzipped)
  assets/index-<hash>.css        ~27 kB (5.8 kB gzipped)
  assets/jszip.min-<hash>.js     ~98 kB (30 kB gzipped) — lazy
```

The JS budget for the entry chunk is **340 kB pre-gzip**; it is currently
**264 kB**.

`jszip` is **not** in that chunk. It is loaded by a dynamic `import()` inside
`lib/inspectBundle.ts`, which only runs when someone actually drops a .zip on
the create wizard, so the menu and the admin pages — which the whole team
opens, and which never inspect a bundle — never fetch those 98 kB. Keep it
that way: import `inspectBundle`, never `jszip`, from anything else.

Beyond that nothing is code-split. The next feature that costs more than a
few kB should lazy-load the creation wizard, which most users never open.

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
`/admin/apps/new`, `/admin/apps/:host`, `/admin/apps/:host/build`. Anything
else redirects to `/` in the browser. (`/admin/apps/new` is declared before
`/admin/apps/:host` so "new" is never read as a hostname; there is a test.)

## Layout

```
src/
  api/
    types.ts          hand-written from portal-api.md
    client.ts         fetch wrapper: CSRF header, ApiError, {error} mapping
    fixtures.ts       the only fake data in the repo
    mock.ts           mock transport (dev-only, lazy-imported)
    client.test.ts
    mock.test.ts      smoke test for the mock's P2a routes
  components/
    Layout.tsx        header, nav (Admin hidden for non-admins), footer
    Button.tsx        primary / outline / ghost / danger, as button or link
    StatusBadge.tsx   awake/starting/asleep/disabled/expired/building/
                      build_failed — one place
    Monogram.tsx      deterministic two-letter card art  (+ test)
    StatTile.tsx      a metric that is a door: click it, the list filters
    Tabs.tsx          underlined tab strip with real tablist ARIA
    Stepper.tsx       the wizard's numbered step indicator
    ZipDropZone.tsx   drag-and-drop (or click) for one .zip
    icons.tsx         ~20 hand-drawn 24-grid glyphs; no icon dependency
    EmailTagEditor.tsx  allowed_emails editor  (+ test)
    ExpiryPicker.tsx    expires_at with a never toggle  (+ test)
    states.tsx        PageHeader / SectionCard / Panel / Notice /
                      Loading / Empty / Error / NoAdminAccess /
                      NoCreateAccess
  hooks/
    useResource.ts        load once, reload on demand, abort on unmount
    useVisiblePolling.ts  15s polling that stops when the tab is hidden
    useAppCollection.ts   admin browse block: search, filter, sort,
                          remembered card/table view  (+ test)
    useKeyAvailability.ts debounced POST /apps/validate-key, with a
                          sequence guard against out-of-order answers
  lib/
    time.ts           epoch <-> local date, relative time
    appDisplay.ts     the short human phrases the cards are made of:
                      access summary, expiry tense, wake hint, greeting,
                      attention flags  (+ test)
    createDraft.ts    the wizard's state and, more importantly, its
                      per-step gating rules as one pure function  (+ test)
    inspectBundle.ts  reads the chosen .zip in the browser: entrypoint,
                      packages, warnings, work caps. Lazy-loads jszip.
                      A port of validate.py's rules  (+ test)
    meContext.ts
  pages/
    MenuPage.tsx           /            card grid  (+ test)
    AdminAppsPage.tsx      /admin       stats + browse block + table/cards
                                        (+ test)
    AdminAppDetailPage.tsx /admin/apps/:host  header, tabs, glance rail
    AppSettingsForm.tsx    the PATCH form, in sections
    AuditLog.tsx           cursor-paginated event list
    NewAppWizard.tsx       /admin/apps/new  4 steps + review  (+ test)
    BuildPage.tsx          /admin/apps/:host/build  honest build status
                                        (+ test)
  App.test.tsx        route gating: admin vs. creator
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
| Two-column definition layout in settings | `Field` in `AppSettingsForm` and in the wizard |
| `EmptyState` with a calm graphic and a way out | `EmptyState` |
| `previews/Create.vue` 4-step stepper: filled tick behind you, ring on you | `Stepper` |
| One card per step, one Continue button gated by that step | `NewAppWizard` |
| Live name-availability with debounce + a sequence guard | `useKeyAvailability` |
| A `<label>`-wrapped drop zone with dragging / chosen / idle skins | `ZipDropZone` |
| Review step that restates every choice before the irreversible click | `ReviewStep` |
| A submit error jumping back to the step that owns the field | 409 → Details |
| "We never guess an expiration for you" — blank by default | Expiry step's two buttons, and `expiryChosen` |

Deliberately **not** ported: their palette, logos and dark mode (the portal is
light-only and Stratevi-branded); teams / organizations / invitations (no
second org, and the reserved access modes stay greyed out); version history,
rollback and delete (P2b); the onboarding tour; the browse block on the *menu*
page, because a client with three tiles does not need to filter three tiles.
Their wizard puts expiry on step 1 and has no review of the bundle; ours gives
expiry its own step (because the API refuses a create without an explicit
choice) and adds a package-confirmation gate.

## P2a: creating an app

The "+ New app" affordance — the header action on `/admin` and the dashed card
on the menu — is **hidden unless `/me` says `can_create`**. Creation is its own
permission and admin does not imply it (`docs/design/portal-p2a.md`,
"Decisions"), so an admin who is not a creator sees nothing rather than a
button that answers 403. A backend that omits `can_create` reads as `false`.

The wizard at `/admin/apps/new`:

| Step | What it decides | What blocks Continue |
|---|---|---|
| Details | label, key, description, task size | no label; no key; the key check is running, has been refused, or could not run |
| Upload | the .zip, and the package list | no file; a client-side rejection; the PUT still in flight; an empty package list; the list not confirmed |
| Access | mode, people, idle timeout, session cap | `users` with nobody on it; idle outside 1–1440; cap outside 0–168 |
| Expiry | a date, or an explicit never | **no choice made** — there is no default |
| Review | nothing; it restates everything | any of the above, re-checked |

Every rule lives in one pure function, `stepBlocker` in `lib/createDraft.ts`,
so "why is Continue disabled?" is answerable and testable. Continue is not
actually `disabled` — clicking it prints the reason, because a dead button
with no explanation is the worst thing a wizard can do.

Things worth knowing:

- **The key check is debounced 400 ms and sequence-guarded.** A changed key
  invalidates the previous verdict immediately, so nobody stares at a stale
  green tick and a Continue that will not go. The API's refusal text is
  printed **verbatim** — the denylist wording is deliberately vague ("pick a
  project codename") and paraphrasing it would leak what it hides.
- **The size check happens before `POST /uploads`.** An over-100 MB zip never
  costs a presigned URL, and the message says what to do (move data to S3).
- **The bundle is inspected in the browser, before the upload.** See below.
- **The upload is XHR, not `fetch`**, purely because `fetch` has no upload
  progress event. That bar is real bytes.
- **The build screen has no progress bar, on purpose.** CodeBuild reports a
  phase, not a fraction. The page shows the phase name, a ticking elapsed
  timer anchored to `started_at`, "first builds take 10–20 minutes because R
  packages compile", the log tail, and a console deep link. It polls every 5 s
  while building and stops the moment the build lands.
- **Success routes to the app's settings page** — unless the creator is not an
  admin, who would only find a refusal card there; they get an "It's live"
  state with the link instead.
- **A failed build is a red `build_failed` chip**, kept in the list, linking
  back to the build screen. Nothing is torn down: retry and delete are P2b.
- **`building` rows link to the build screen, not to settings**, since there
  is nothing yet to configure.

### Bundle inspection is client-side (`lib/inspectBundle.ts`)

There is no `POST /uploads/inspect`, and there must not be one:
`docs/design/portal-api.md`, "Bundle inspection is CLIENT-SIDE". The proxy
task sits in the request path for **every** app on the platform, so making it
download and unzip a 100 MB bundle would put other people's page loads behind
that work on 0.25 vCPU. A router should not become a worker. The browser
already holds the bytes, and reading them first also means a bundle with no
entrypoint fails in a second instead of after a 100 MB PUT.

So, the moment a .zip passes the name/size check and **before** `POST
/uploads`:

1. `jszip` is dynamically imported and the zip's central directory is read.
   Only the small text files below are ever decompressed.
2. **Entrypoint**, by the same rules as `validate.py` (see below).
3. **Packages**, in the spec's order: `renv.lock` (the keys of its
   `Packages` object) → `packages.txt` (one per line; blanks and `#`
   comments dropped) → a scan of `.R`/`.Rmd` files for `library(x)`,
   `require(x)` and `requireNamespace("x")`. A declared file that cannot be
   read does not vanish silently: it adds a warning and the next source is
   tried. Where the list came from is shown to the user.
4. The entrypoint, the source and the editable list are shown for an
   **explicit confirmation** (portal-p2a.md: never silently guess), while the
   upload runs in the background.

If any of that fails — not a zip, no entrypoint, two candidate directories,
a cap hit — the wizard says exactly why and the user types the list by hand.
**It never blocks the upload**, because this is a UX affordance, not a
security control.

**`proxy-app/buildspec/validate.py` is the only authority.** It re-checks
everything server-side (zip-slip, symlinks, entry count, extracted size,
entrypoint), so a client that lies gets a failed build, not a bad app.

The entrypoint rule is a deliberate mirror of that file's
`resolve_entrypoint()`, because a mismatch means the wizard says "looks good"
and the build then rejects it:

- `app.R` at the bundle root wins; otherwise `ui.R` **and** `server.R`
  together, reported as `ui.R+server.R`.
- Exactly one of `ui.R`/`server.R` at the root is a rejection naming the
  missing half — and it is checked **before** the wrapper scan, so a bundle
  with a stray `ui.R` at the root is rejected even if a valid wrapper exists.
- Otherwise: root directories that themselves contain an entrypoint are
  candidates. **Exactly one** is the wrapper the build flattens (said out
  loud as a warning, and it becomes the app root for `renv.lock` etc.);
  **more than one** is a rejection naming them; **none** is a rejection that
  lists the top level.
- Flattening a wrapper whose contents collide with a name already at the
  root is a rejection, as it is in the build.
- `__MACOSX/` and `.DS_Store` / `Thumbs.db` / `desktop.ini` are ignored
  entirely; backslashes are treated as separators.

Work caps (`DEFAULT_LIMITS`), so a huge or hostile zip produces a message
rather than a frozen tab: 5000 entries (validate.py's own ceiling), 1 MB per
text file read, 200 `.R` files scanned at 256 kB each and 4 MB in total, and
a 20 s deadline enforced both by a timer and by a check between steps.
Nothing outside those files is ever decompressed.

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
- **Creating is gated on `can_create`, not `is_admin`**, in the nav
  affordances and again on the routes (`RequireCreate` in `App.tsx`). The API
  is the real enforcement; the UI just refuses to dangle a 403.

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

### P2a additions — two things the contract does not settle

(The third, "nothing returns the detected entrypoint and package list", is
**resolved**: there is no inspect route and there will not be one. The
wizard reads the zip in the browser before uploading — see "Bundle
inspection is client-side" above and portal-api.md's section of the same
name. `POST /apps` still takes `packages` as an input; that list is the one
the user confirmed.)

9. **`cpu`/`memory` units.** "One of the two allowed sizes" does not say in
   what. The UI sends Fargate task units — `512`/`2048` and `4096`/`16384`
   (numbers, not strings), because that is what `RegisterTaskDefinition`
   takes. If the API wants `"0.5 vCPU"` strings, say so.
10. **Whether the presigned PUT signs `Content-Type`.** The client sets no
    `Content-Type` header, so the browser sends the `File`'s own type
    (`application/zip`, or empty from some file pickers). If the presign is
    generated with a signed `ContentType`, that mismatch is a silent 403 on
    the PUT. Generate the URL without one, or pin the value and say so here.

Smaller things, resolved rather than flagged: the build screen assumes
`started_at` is epoch seconds like every other timestamp and falls back to
`elapsed_s` when it is absent; `phase` is treated as an open string and
printed as-is; `log_tail` is assumed oldest-first (it is rendered in order,
scrolled to the bottom).
