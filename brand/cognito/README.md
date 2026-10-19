# Cognito managed-login branding

The Stratevi look for the hosted sign-in page — the page everyone lands on
before the portal or any proxied app. Dark navy, one centred card, the brand
mark and wordmark above it, Microsoft365 as the primary path.

Everything needed to reproduce it lives in this directory, and `targets/`
lists every pool it is installed into.

| File | What it is |
|---|---|
| `settings.json` | The whole branding document — colours, radii, layout, which chrome is on |
| `assets/` | The generated images, plus `manifest.json` mapping each to its Cognito asset category |
| `build-assets.mjs` | Regenerates `assets/` from `../stratevi-brandmark.svg` and `../ai-stratevi-lockup.svg` |
| `targets/` | One file per destination: pool, client, region, the pool's IdP name, and the URLs for the closing message |
| `apply.ps1` | Pushes `settings.json` + `assets/` to one target's live style. Re-runnable |
| `preview.html` | A local approximation of the result. Open it off disk before applying |

## Why this is not Terraform

Provider 5.x has no `aws_cognito_managed_login_branding` resource. The
association itself is already a documented manual step — `proxy/cognito.tf`
carries the banner and `docs/GOTCHAS.md` the symptom ("Login pages
unavailable. Please contact an administrator."), because every new app client
needs one before its `/login` will render at all.

Without this directory the *style* would be console-only state: invisible to
review, unreproducible after a client is recreated, and lost the moment
someone clicks around in the branding editor. The repo holds the desired
state; `apply.ps1` makes the account match it. When the provider grows the
resource, this becomes a `terraform import` and these files become the inputs.

## One style, many pools

A Cognito managed-login style belongs to exactly one **(user pool, app
client)** pair. There is no account-level style, no cross-pool reference, and
no "import from another pool" in the console — so a pool with no style of its
own shows an empty branding editor, which is correct behaviour and not a bug.
Standardising the look across Stratevi therefore means *installing the same
document in several places*, which is what `targets/` is for.

| Target | Pool | Serves |
|---|---|---|
| `shiny-platform` | `us-east-1_LI3CZpwAF` | The shared client for the portal *and* every proxied app (`proxy/cognito.tf`) — one style for the whole platform |
| `amcp-dossier` | `us-east-1_vsrCYXPkB` | The AMCP dossier writer on Railway. Its provider is named `Microsoft`, so the button reads "Continue with Microsoft" |

**The provider name differs per pool and the style is keyed on it.** The Entra
federation is called `Microsoft365` in the platform pool and `Microsoft` in
`stratevi-dossier`. That string is both the key of
`components.idpButton.custom` in `settings.json` and the `ResourceId` of the
two IdP-icon assets. `settings.json` carries the platform literal and
`apply.ps1` renames it per target; the script throws if the key it expects to
rename has disappeared, because Cognito does **not** error on a provider name
the pool lacks — the button silently falls back to the grey `standard`
treatment and you ship a correct-looking page with the wrong button.

What branding cannot fix: the button's *label* is the provider's name, so the
dossier page reads "Sign in with Microsoft" where the platform reads "Sign in
with Microsoft365". Only renaming a provider — recreating it, new client
secret from Entra, repointing its clients — makes those match.

## Apply

Read-only first — this resolves the style, builds the request, validates its
size, and stops:

```powershell
cd brand\cognito
.\apply.ps1 -Target amcp-dossier -DryRun
```

Then, for real:

```powershell
.\apply.ps1 -Target amcp-dossier
```

`-Target` is mandatory and deliberately undefaulted: a defaulted destination
is how you restyle the wrong pool by accident. The script resolves the
branding id from the *client* rather than hardcoding it, because the
association is recreated — with a new id — whenever the client is.

It ends by printing a hosted-UI URL that renders the page. **That URL cannot
complete a sign-in**: the relying party rejects a callback it did not start
(for the platform, the ALB's `AuthMissingStateParam` — see
`docs/GOTCHAS.md`). To exercise a real sign-in, start from the target's
`entryPoint`.

### Check for drift before you apply

`update-managed-login-branding` replaces the whole Settings document, so
re-applying overwrites anything done in the console since the last run. Dump
the live document and compare it against `settings.json` first:

```powershell
aws cognito-idp describe-managed-login-branding --user-pool-id <pool> --managed-login-branding-id <id> --region us-east-1 --query ManagedLoginBranding.Settings
```

**Reconciled 2026-10-15.** The live `shiny-platform` style had diverged from
this repo. A full diff of the Settings document and all ten assets found four
differences, two of them deliberate and two accidental:

| | Live | This repo had | Verdict |
|---|---|---|---|
| `categories.global.colorSchemeMode` | `DYNAMIC` | `DARK` | **live wins** — light mode renders correctly and some people sign in on a light OS |
| `FORM_LOGO` | PNG raster of the official corporate lockup — mark, `stratevi®`, STRATEGY + EVIDENCE | a 240×60 platform SVG reading "Dashboards and models, on demand" | **live wins** — the corporate lockup is the editorial choice |
| `primaryButton` + `secondaryButton` | Cognito stock blue `539fe5` / `0972d3` | Stratevi teal `57C4C5` | **repo wins** — the stock blue contradicts the colour mapping below |
| `categories.signUp` | present | not written | immaterial; AWS flags it unimplemented |

Eight of the ten assets were byte-identical all along, so only the buttons
were ever genuinely wrong. The repo was changed to match live on the two
deliberate points: `colorSchemeMode` is now `DYNAMIC`, and the corporate
lockup was **captured out of the live style into
`assets/form-logo-corporate-{dark,light}.png`** and put in the manifest in
place of the platform SVGs. The repo therefore describes the live style
completely — no asset is unmanaged — and the lockup travels to every other
pool, which is the whole point of `targets/`.

`build-assets.mjs` does **not** generate those two PNGs. They were rasterised
by Cognito's own console uploader and recovered with
`describe-managed-login-branding`; regenerating assets will not recreate them
and must not delete them. `form-logo-*.svg`, the 240×60 platform composition
carrying "Dashboards and models, on demand", is kept but no longer applied —
swap the manifest's two `FORM_LOGO` rows back to the SVGs to return to it.

**Applied 2026-10-15.** On `shiny-platform`: the eight button colour values
changed, the `signUp` category went away, and nothing else moved — the
lockup and the two stock `PAGE_BACKGROUND` images were left untouched.
Verified in both colour schemes; "Sign in" is teal and matches "Continue with
Microsoft365". On `amcp-dossier`, which until then was still on Cognito's
provided values: the full style went on, all ten assets, verified in both
schemes. A dry run against either target is now a genuine no-op.

The most consistent account of how this happened: the repo was applied, then
someone worked in the console branding editor, which resets the fields it
exposes (the two button styles, the colour-scheme mode), adds a `signUp`
category, and re-uploads logos through an uploader that rasterises SVG to
PNG. The per-IdP button colours survived precisely because the editor does
not expose them — which is why the teal "Continue with Microsoft365" button
was still correct while "Sign in" was not.

### Comparing bytes: normalise line endings first

`core.autocrlf` is `true` here and `.gitattributes` pins only `*.yml`,
`*.sh`, `Dockerfile*` and `*.template` to LF — **not** `*.svg`. So the SVGs in
the working tree have CRLF, `apply.ps1` sends those bytes, and a style applied
from a different checkout can hold LF. Comparing raw bytes therefore reports
every SVG as changed when the content is identical; it reported eight false
differences during this investigation before the cause was spotted. Strip
`\r\n` → `\n` on both sides before concluding anything. The `.ico` files are
binary, so git leaves them alone and they compare directly.

## Regenerate the images

```powershell
node build-assets.mjs
```

Pure string work except the two `.ico` files, which need a rasterizer that is
deliberately *not* a dependency of this repo:

```powershell
mkdir $env:TEMP\rast; cd $env:TEMP\rast; npm init -y; npm i @resvg/resvg-js
cd <repo>\brand\cognito
$env:RESVG_MODULE = "file:///$env:TEMP/rast/node_modules/@resvg/resvg-js/index.js"
node build-assets.mjs
```

Without it the script still writes every SVG and skips the `.ico` pair with a
warning. Do **not** add this to `portal-ui/`.

The two files in `../` are read only and never modified. The corporate lockup
ships with black paths; `build-assets.mjs` recolours a crop of it at build
time.

## Which asset is which page element

| Asset | Category | Where it appears |
|---|---|---|
| `form-logo-corporate-{dark,light}.png` | `FORM_LOGO` | Above the card: the corporate lockup — mark, `stratevi®`, STRATEGY + EVIDENCE. `formInclusion: OUT` is what puts it outside the card rather than inside it. **Not generated by `build-assets.mjs`** — recovered from the live style; see the reconciliation note above |
| `form-logo-{dark,light}.svg` | — (kept, not applied) | The 240×60 platform alternative, with "Dashboards and models, on demand" baked in. Swap it into the manifest to use it instead |
| `footer-logo-{dark,light}.svg` | `PAGE_FOOTER_LOGO` | Centred in the page footer: the corporate `ASSEMBLED INTELLIGENCE \| stratevi` lockup, flattened to one muted grey |
| `favicon-{dark,light}.svg` | `FAVICON_SVG` | Browser tab, modern browsers. The mark alone, 16×16 |
| `favicon-{dark,light}.ico` | `FAVICON_ICO` | Browser tab / bookmarks, legacy. PNG-in-ICO at 16, 32, 48 |
| `idp-microsoft365-{dark,light}.svg` | `IDP_BUTTON_ICON`, `ResourceId` from the target | The icon inside the "Sign in with ..." button. `ResourceId` is how Cognito knows which provider an icon belongs to — it must match the IdP's name in the pool, so `apply.ps1` supplies it from `targets/`. The manifest's own `resourceId` is now just a flag meaning "this is an IdP icon"; the filename says microsoft365 but it is the Microsoft logo either way |

Cognito's own conventions, followed here: logos are 240×60, favicons and IdP
icons 16×16. Light-mode copies are uploaded for every asset because
`colorSchemeMode` is `DYNAMIC` — the page follows each viewer's OS
preference, so both sets render in anger and both need to be right.

## Colour mapping

| Element | Colour |
|---|---|
| Page background | Navy `#0C111B` |
| Card (form container) and its border | Gray `#283340`, border `#3A4757` |
| Inputs | `#1E2732` on the card — recessed, border `#45525F` |
| Primary button, and the Microsoft365 button | Teal `#57C4C5`, navy text (9.2:1) |
| Secondary / other IdP buttons | Card-coloured, `#4A5765` outline, teal on hover |
| Headings / body / descriptions | `#F2F5F8` / `#C2CCD9` / `#9BA9BC` |
| Links | Accent Blue-Purple `#74A3D7`, brightening to Stratevi Blue `#40C4DF` on hover |
| Focus ring | Stratevi Blue `#40C4DF` |
| Success indicator | Stratevi Green `#51BB7F` |
| Radii | 8px everywhere — form, buttons, inputs, dropdowns, alerts — matching `portal-ui`'s `--radius: 0.5rem` |

Stratevi Teal carries the primary action, per the palette note that it is the
one to reach for when only one colour is used. Green is kept for "this
worked"; the blues for wayfinding. The greys between navy and gray
(`#3A4757`, `#45525F`, `#4A5765`, `#1E2732`) are steps on the navy→gray ramp,
not new brand colours.

Colours are `RRGGBBAA` — eight hex digits, alpha included. Six will be
ignored.

## What the branding system will not let us control

- **Any text on the page.** "Sign in", "Sign in with Microsoft365", "Forgot
  your password?", every label and error — all Cognito's, none of them
  editable. The `lang` query parameter picks a translation and that is the
  whole of it. This is why the tagline is baked into `form-logo-*.svg`: an
  image is the only way to place a line of our own copy. Editing it is a
  one-line change at the top of `build-assets.mjs`, then a rebuild.
- **The IdP button's label.** It is the provider's *name* in the pool, so the
  button reads "Sign in with Microsoft365" because the provider is called
  `Microsoft365`. Renaming it means re-pointing every client.
- **Fonts.** No typeface control at all; Cognito's stack is what renders.
- **Layout beyond the switches in `settings.json`** — header on/off, footer
  on/off, logo inside/outside the card, form alignment, spacing density. There
  is no custom CSS and no HTML injection.
- **Deleting an asset.** Create/update are a PATCH: they add and replace, they
  do not remove. Cognito's two stock `PAGE_BACKGROUND` images (a 993 KB JPEG
  among them) stay attached to the style. `pageBackground.image.enabled:
  false` stops them rendering, which is the whole of the fix; genuinely
  removing them needs `delete-managed-login-branding` plus a fresh
  `create-managed-login-branding`, which would break `/login` until the new
  style exists. Not worth it.
- **`signUp`, `instructions`, `sessionTimerDisplay` and `languageSelector`**
  are in the schema but flagged by AWS as not implemented. Left alone.

### The one setting taken on trust

`components.form.logo.formInclusion` is `"OUT"`. `"IN"` is what the live
document contains and is therefore the only value confirmed to exist; `"OUT"`
is the obvious complement and is what puts the lockup above the card instead
of inside it, which is the whole point of the composition. If after applying
the lockup renders *inside* the card, that value is wrong — change it to
`"IN"` and accept the logo-in-card layout, or find the real enum in the
console's branding editor (it round-trips through the same document).

## Size limits

A create/update request must be ≤ 2 MB in total and each asset ≤ 1,000,000
bytes. Ours is ~0.15 MB across 10 assets; `apply.ps1` checks both and refuses
rather than sending something that will fail.
