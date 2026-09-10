import { api } from '../api/client'
import type { MenuApp } from '../api/types'
import { StatusBadge } from '../components/StatusBadge'
import { EmptyState, ErrorState, Loading, PageHeading } from '../components/states'
import { useResource } from '../hooks/useResource'
import { useVisiblePolling } from '../hooks/useVisiblePolling'
import { useMe } from '../lib/meContext'

/**
 * dashboards.tools.stratevi.com. External clients land here, so it stays
 * quiet: a heading, tiles, nothing that looks like a control panel.
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
      <PageHeading
        title="Your tools"
        subtitle={
          apps.length > 0
            ? 'Sleeping tools take about a minute to start the first time you open them.'
            : undefined
        }
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
        </ul>
      )}
    </>
  )
}

function MenuTile({ app }: { app: MenuApp }) {
  return (
    <li>
      <a
        href={app.url}
        className="group flex h-full flex-col rounded-card border border-line bg-surface p-5 shadow-card transition-all hover:-translate-y-px hover:border-accent/40 hover:shadow-card-hover"
      >
        <div className="flex items-start justify-between gap-3">
          <h2 className="text-[15px] font-semibold leading-snug text-ink group-hover:text-accent">
            {app.label || app.host}
          </h2>
          <StatusBadge state={app.live_state} />
        </div>

        {app.description ? (
          <p className="mt-2.5 line-clamp-4 text-sm leading-relaxed text-muted">
            {app.description}
          </p>
        ) : null}

        <p className="mt-auto pt-5 font-mono text-xs text-faint">{app.host}</p>
      </a>
    </li>
  )
}
