import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { LiveState, MenuApp } from '../api/types'
import { LinkField } from '../components/LinkField'
import { MetaRow, MetaRows } from '../components/MetaRow'
import { Monogram } from '../components/Monogram'
import { StatusBadge } from '../components/StatusBadge'
import { EmptyState, ErrorState, Loading, PageHeader } from '../components/states'
import {
  AlertIcon,
  BoltIcon,
  CalendarClockIcon,
  ClockIcon,
  ExternalIcon,
  PlusIcon,
  PowerIcon,
} from '../components/icons'
import { useResource } from '../hooks/useResource'
import { useVisiblePolling } from '../hooks/useVisiblePolling'
import {
  expiryPhrase,
  expirySummary,
  firstNameFromEmail,
  greeting,
  liveStateHint,
  liveStateTone,
  menuPulse,
} from '../lib/appDisplay'
import { relativeTime } from '../lib/time'
import { useMe } from '../lib/meContext'

/**
 * dashboards.tools.stratevi.com. External clients land here, so it stays
 * quiet: a greeting, a grid of cards, nothing that looks like a control
 * panel. The layout is assembled.work's dashboard — greeting, one-sentence
 * pulse, card grid — with their search/filter/sort browse block left off,
 * because a client with three tiles does not need to filter three tiles.
 */
export function MenuPage() {
  const me = useMe()
  const { data, error, loading, reload } = useResource('menu', (signal) => api.menu(signal))

  useVisiblePolling(() => reload({ quiet: true }), 15_000)

  if (loading) return <Loading label="Loading your tools" />
  if (error) return <ErrorState error={error} onRetry={() => reload()} />

  const apps = data ?? []

  return (
    <>
      <PageHeader
        title={`${greeting()}, ${firstNameFromEmail(me?.email)}`}
        description={menuPulse(apps.map((a) => a.live_state))}
        meta={me?.email ? `Signed in as ${me.email}` : undefined}
      />

      {apps.length === 0 ? (
        <EmptyState title="Nothing is shared with you yet">
          <p>
            {me?.email ? (
              <>
                Your account <span className="font-medium text-foreground">{me.email}</span> isn’t on the
                access list for any tool yet.
              </>
            ) : (
              <>Your account isn’t on the access list for any tool yet.</>
            )}{' '}
            Ask the person who shared this link with you, or email{' '}
            <a className="text-azure hover:underline" href="mailto:support@stratevi.com">
              support@stratevi.com
            </a>
            .
          </p>
        </EmptyState>
      ) : (
        <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {apps.map((app) => (
            <MenuTile key={app.host} app={app} />
          ))}
          {/* Creation is its own permission — admin does not imply it. */}
          {me?.can_create ? <NewAppTile /> : null}
        </ul>
      )}
    </>
  )
}

/**
 * The glyph beside a tile's readiness line. The wording and the tone come
 * from `appDisplay`; only the drawing is decided here.
 */
function readinessIcon(state: LiveState | string): ReactNode {
  switch (state) {
    case 'awake':
      return <BoltIcon />
    case 'build_failed':
      return <AlertIcon />
    case 'expired':
      return <CalendarClockIcon />
    case 'disabled':
      return <PowerIcon />
    default:
      // asleep, starting, building — all of them are "wait a moment".
      return <ClockIcon />
  }
}

/**
 * One tile, on assembled.work's `PreviewCard`: art band with a status chip,
 * then the name, then the **link field**, then metadata rows with small
 * outline icons.
 *
 * Two departures, both forced by what this product is:
 *
 * - The art band stays a monogram. Their thumbnails are screenshots of public
 *   static sites; ours are Cognito-gated and asleep, so there is nothing to
 *   capture on demand (docs/design/portal-visual-reference.md).
 * - The only metadata the *menu* contract carries is `live_state`, so the one
 *   row is readiness. Their "Expires"/"Updated"/"Shared by" rows would need
 *   `expires_at` and `last_active` on `GET /api/v1/menu`, which returns
 *   neither — and a row that always says "—" is worse than no row.
 */
function MenuTile({ app }: { app: MenuApp }) {
  const hint = liveStateHint(app.live_state)
  const label = app.label || app.host
  const expiry = expirySummary(app.expires_at)

  return (
    // min-w-0: the host is one unbreakable token, and without this the grid
    // track widens to fit it and the card overflows a phone screen.
    <li className="min-w-0">
      {/* Not an <a> around the whole card any more: the link field inside it
          has its own buttons, and an anchor cannot contain an anchor. The
          title's link is stretched over the card instead (`after:inset-0`),
          so the card is still one click target with one accessible name. */}
      <div className="group relative flex h-full flex-col overflow-hidden rounded-lg border border-border bg-card shadow-sm transition-all hover:-translate-y-px hover:border-azure/40 hover:shadow-md focus-within:border-azure/40">
        {/* The app leads, the way a screenshot would if we had one; the chip
            rides the art so it never competes with the title for width. */}
        <div className="relative">
          <Monogram
            name={label}
            seed={app.host}
            className="aspect-[16/5] w-full border-b border-border/60"
          />
          <StatusBadge
            state={app.live_state}
            className="absolute right-2.5 top-2.5 shadow-sm"
            title={hint ?? undefined}
          />
        </div>

        <div className="flex flex-1 flex-col p-5">
          <h2 className="flex items-start gap-1.5 text-[15px] font-semibold leading-snug text-foreground group-hover:text-azure">
            <a
              href={app.url}
              className="min-w-0 rounded-sm after:absolute after:inset-0 after:content-['']"
            >
              {label}
            </a>
            <ExternalIcon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground/70 opacity-0 transition-opacity group-hover:opacity-100" />
          </h2>

          {app.description ? (
            <p className="mt-2 line-clamp-3 text-sm leading-relaxed text-muted-foreground">
              {app.description}
            </p>
          ) : null}

          {/* z-10: above the stretched title link, so Copy and Open are real
              buttons rather than another way to navigate to the app. */}
          <div className="relative z-10 mt-auto space-y-2.5 pt-5">
            <LinkField
              url={app.url}
              kind={app.live_state === 'awake' ? 'live' : 'idle'}
            />
            <MetaRows>
              {hint ? (
                <MetaRow
                  icon={readinessIcon(app.live_state)}
                  tone={liveStateTone(app.live_state)}
                >
                  {hint}
                </MetaRow>
              ) : null}
              {/* Expiry is the reader's business, not just an admin's: an
                  app they rely on vanishing next week is something they
                  should find out from the tile, not from a 410 page. */}
              <MetaRow
                icon={<CalendarClockIcon />}
                tone={expiry.tone === 'soon' || expiry.tone === 'past' ? 'warn' : 'muted'}
              >
                {expiryPhrase(expiry)}
              </MetaRow>
              <MetaRow icon={<ClockIcon />} tone="muted">
                {app.last_active
                  ? `Active ${relativeTime(app.last_active)}`
                  : 'Not opened yet'}
              </MetaRow>
            </MetaRows>
          </div>
        </div>
      </div>
    </li>
  )
}

/**
 * The door to the P2a wizard. Shown only to people the `__config__` row lists
 * as creators — hidden, not disabled, for everyone else, so nobody is dangled
 * an affordance that answers 403.
 */
function NewAppTile() {
  return (
    <li>
      <Link
        to="/admin/apps/new"
        className="group flex h-full min-h-[13rem] flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border bg-card/50 p-5 text-center transition-colors hover:border-azure/40 hover:bg-accent/50"
      >
        <span className="flex h-9 w-9 items-center justify-center rounded-full bg-background text-muted-foreground/70 transition-colors group-hover:bg-accent group-hover:text-azure">
          <PlusIcon className="h-4 w-4" />
        </span>
        <p className="text-sm font-medium text-muted-foreground group-hover:text-azure">New app</p>
        <p className="max-w-[16rem] text-xs leading-relaxed text-muted-foreground/70">
          Upload a Shiny app and get a private, protected link. It builds in about
          15 minutes.
        </p>
      </Link>
    </li>
  )
}
