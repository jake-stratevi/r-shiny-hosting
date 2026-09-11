# Cognito managed-login branding

The Stratevi look for the hosted sign-in page — the page everyone lands on
before the portal or any proxied app. Dark navy, one centred card, the brand
mark and wordmark above it, Microsoft365 as the primary path.

Everything needed to reproduce it lives in this directory. **Nothing here has
been applied.** Run `apply.ps1` when the design is blessed.

| File | What it is |
|---|---|
| `settings.json` | The whole branding document — colours, radii, layout, which chrome is on |
| `assets/` | The generated images, plus `manifest.json` mapping each to its Cognito asset category |
| `build-assets.mjs` | Regenerates `assets/` from `../stratevi-brandmark.svg` and `../ai-stratevi-lockup.svg` |
| `apply.ps1` | Pushes `settings.json` + `assets/` to the live style. Re-runnable |
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

**One client, one style.** `1senki56hh2ngv7neuqhot6gv8` is the shared client
for the portal *and* every proxied app (see `proxy/cognito.tf`), so this single
style is the sign-in page for the whole platform. There is nothing per-app to
maintain.

## Apply

Read-only first — this resolves the style, builds the request, validates its
size, and stops:

```powershell
cd brand\cognito
.\apply.ps1 -DryRun
```

Then, for real:

```powershell
.\apply.ps1
```

Defaults are our pool (`us-east-1_LI3CZpwAF`) and shared client
(`1senki56hh2ngv7neuqhot6gv8`); both are parameters. The script resolves the
branding id from the *client* rather than hardcoding it, because the
association is recreated — with a new id — whenever the client is.

It ends by printing a hosted-UI URL that renders the page. **That URL cannot
complete a sign-in**: the ALB rejects a callback it did not start
(`AuthMissingStateParam`, see `docs/GOTCHAS.md`). To exercise a real sign-in,
start at <https://shinyplatform.tools.stratevi.com>.

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
| `form-logo-{dark,light}.svg` | `FORM_LOGO` | Above the card: gradient hex mark + `stratevi` wordmark, tagline beneath. `formInclusion: OUT` is what puts it outside the card rather than inside it |
| `footer-logo-{dark,light}.svg` | `PAGE_FOOTER_LOGO` | Centred in the page footer: the corporate `ASSEMBLED INTELLIGENCE \| stratevi` lockup, flattened to one muted grey |
| `favicon-{dark,light}.svg` | `FAVICON_SVG` | Browser tab, modern browsers. The mark alone, 16×16 |
| `favicon-{dark,light}.ico` | `FAVICON_ICO` | Browser tab / bookmarks, legacy. PNG-in-ICO at 16, 32, 48 |
| `idp-microsoft365-{dark,light}.svg` | `IDP_BUTTON_ICON`, `ResourceId: Microsoft365` | The icon inside the "Sign in with Microsoft365" button. `ResourceId` is how Cognito knows which provider an icon belongs to — it must match the IdP's name in the pool |

Cognito's own conventions, followed here: logos are 240×60, favicons and IdP
icons 16×16. Light-mode copies are uploaded even though `colorSchemeMode` is
`DARK`, so flipping to `DYNAMIC` later needs no new files.

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
