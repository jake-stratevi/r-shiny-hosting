# Portal visual reference — assembled.work, observed

Taken from screenshots Jake supplied 2026-09-10 of the logged-in product
(Dashboard, My Apps, Shared With Me, Create). Written down because the Vue
source gives structure and tokens but not proportion, density or the small
components that actually make it feel like itself. Their app.css identity
comment is the other half of this: warm ink-on-paper neutrals, ONE azure
accent for focus and wayfinding, ink primaries, status colours reserved for
state.

**Scope note:** we copy the design language, not the brand. Stratevi name and
mark, their layout and palette. Jake confirmed this again in the same
session.

## Shell

- **Collapsed icon rail by default**, ~64 px, icons only, tooltip on hover
  (the "Dashboard" tooltip is visible mid-hover in one shot). Logo mark top
  left; user avatar initials ("JP") pinned bottom left.
- **Top bar** is just a sidebar-toggle button plus a breadcrumb trail
  ("My Apps › Create"). No search, no actions — the page owns those.
- Content is a wide centred column on a near-black ground; cards sit on a
  slightly lifted surface with a hairline border and generous radius.

## Page header pattern

Big display title, one muted sentence under it, page actions right-aligned
on the same line as the title.

- Dashboard: "Good afternoon, Jake" / "All quiet — 2 of your 2 apps are
  live." Actions: `Tour`, `+ New app`.
- My Apps: "My Apps" / "Every app link you've created, and who can open
  it." Actions: `Show disabled`, `+ New app`.
- Shared With Me: title / "Apps other people have shared with you…"
  Actions: `Show expired`, `Only shared with me`.

The subtitle is a real sentence about the reader's situation, not a label.
Worth copying: it is most of why the product feels considered.

**In dark mode the primary button is light with dark text** — the ink
inversion. Secondary actions are plain text with an icon, no border.

## Stat tiles (dashboard)

Small rounded cards, each a coloured rounded-square icon (emerald for live,
azure for all) beside a muted caption over a large number. Two of them, left
aligned, not stretched across the width.

## Filter row

One line, above the collection: search field with a magnifier and "Search by
name or client" placeholder, then two select dropdowns ("All statuses",
"Recently updated"), then a two-button grid/list view toggle on the far
right. Present identically on every collection page.

## App card

Top to bottom:

1. **Screenshot thumbnail** of the site, with a green `● Live` pill floating
   at its top right.
2. Name (large, semibold); optional client/brand line under it, muted.
3. **Link chip** — a bordered, rounded, slightly inset box containing a
   paper-plane icon, the hostname in a monospace face, a copy-to-clipboard
   button and an open-in-new-tab button. It scrolls horizontally inside
   itself for long hostnames rather than growing the card (their
   `scroll-x-thin`).
4. **Metadata rows**, each a small outline icon plus text: "Shared by …"
   (person), "Expires Sep 30, 2026" / "Never expires" (calendar-clock),
   "Updated Sep 8, 2026" (clock), "Added Jul 23, 2026" (calendar).

**We cannot do the thumbnail.** Their sites are public static pages; our apps
are behind Cognito and asleep most of the time, so there is nothing to
screenshot on demand. Keep the deterministic monogram band already built —
it occupies the same slot and reads as deliberate. (A real thumbnail is
possible later: capture one headlessly after a successful wake and cache it.
Not now.)

Everything else on the card transfers directly, and the **link chip is the
highest-value single component to copy** — it is on every card, and ours has
no equivalent.

## Create wizard

- Breadcrumb "My Apps › Create". Title "New app" with the sentence "Upload a
  site and share it with exactly the right people."
- **Stepper**: numbered circles joined by dashes — `1 App details — 2 Upload
  — 3 Access — 4 Review`. Current step's circle is ringed in azure with its
  label in full-strength text; the rest are muted.
- One card per step. The card leads with a tinted rounded-square icon beside
  a heading and a one-line explanation of what this step is for.
- Fields are label-above-input, comfortable vertical rhythm, inputs are dark
  with a hairline border.
- **The live hostname preview is a tinted azure callout**, not a caption:
  a small "Your app's link" label over the URL in monospace.
- Expiry: a date input plus a "This app never expires" checkbox, with
  helper text explaining both paths. (Ours is stricter — an explicit choice
  is required, which is better; keep that.)
- Footer: `Back` as plain text on the left, `Continue` as the filled primary
  on the right.

## What we already match

Greeting-plus-pulse header, stat tiles that filter, card grid with status
chips, search/status/sort/view-toggle row, four-step wizard with a stepper
and a review step, sectioned detail page. The gap was never the interaction
design — it was the shell, the theme and the small components.
