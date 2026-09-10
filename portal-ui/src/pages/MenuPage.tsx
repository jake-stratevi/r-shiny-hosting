import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { MenuApp } from '../api/types'
import { Monogram } from '../components/Monogram'
import { StatusBadge } from '../components/StatusBadge'
import { EmptyState, ErrorState, Loading, PageHeader } from '../components/states'
import { ExternalIcon, PlusIcon } from '../components/icons'
import { useResource } from '../hooks/useResource'
import { useVisiblePolling } from '../hooks/useVisiblePolling'
import { firstNameFromEmail, greeting, liveStateHint, menuPulse } from '../lib/appDisplay'
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
                Your account <span className="font-medium text-ink">{me.email}</span> isn’t on the
                access list for any tool yet.
              </>
            ) : (
              <>Your account isn’t on the access list for any tool yet.</>
            )}{' '}
            Ask the person who shared this link with you, or email{' '}
            <a className="text-accent hover:underline" href="mailto:support@stratevi.com">
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

function MenuTile({ app }: { app: MenuApp }) {
  const hint = liveStateHint(app.live_state)
  const label = app.label || app.host

  return (
    // min-w-0: the host is one unbreakable token, and without this the grid
    // track widens to fit it and the card overflows a phone screen.
    <li className="min-w-0">
      <a
        href={app.url}
        className="group flex h-full flex-col overflow-hidden rounded-card border border-line bg-surface shadow-card transition-all hover:-translate-y-px hover:border-accent-line hover:shadow-card-hover"
      >
        {/* The app leads, the way a screenshot would if we had one; the chip
            rides the art so it never competes with the title for width. */}
        <div className="relative">
          <Monogram
            name={label}
            seed={app.host}
            className="aspect-[16/5] w-full border-b border-line-soft"
          />
          <StatusBadge
            state={app.live_state}
            className="absolute right-2.5 top-2.5 shadow-card"
            title={hint ?? undefined}
          />
        </div>

        <div className="flex flex-1 flex-col p-5">
          <h2 className="flex items-start gap-1.5 text-[15px] font-semibold leading-snug text-ink group-hover:text-accent">
            <span className="min-w-0">{label}</span>
            <ExternalIcon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-faint opacity-0 transition-opacity group-hover:opacity-100" />
          </h2>

          {app.description ? (
            <p className="mt-2 line-clamp-3 text-sm leading-relaxed text-muted">
              {app.description}
            </p>
          ) : null}

          <div className="mt-auto pt-5">
            <p className="truncate font-mono text-xs text-faint">{app.host}</p>
            {hint ? (
              <p
                className={`mt-1 text-xs ${
                  app.live_state === 'awake' ? 'text-emerald-700' : 'text-faint'
                }`}
              >
                {hint}
              </p>
            ) : null}
          </div>
        </div>
      </a>
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
        className="group flex h-full min-h-[13rem] flex-col items-center justify-center gap-2 rounded-card border border-dashed border-line bg-surface/50 p-5 text-center transition-colors hover:border-accent-line hover:bg-accent-soft/50"
      >
        <span className="flex h-9 w-9 items-center justify-center rounded-full bg-canvas text-faint transition-colors group-hover:bg-accent-soft group-hover:text-accent">
          <PlusIcon className="h-4 w-4" />
        </span>
        <p className="text-sm font-medium text-muted group-hover:text-accent">New app</p>
        <p className="max-w-[16rem] text-xs leading-relaxed text-faint">
          Upload a Shiny app and get a private, protected link. It builds in about
          15 minutes.
        </p>
      </Link>
    </li>
  )
}
