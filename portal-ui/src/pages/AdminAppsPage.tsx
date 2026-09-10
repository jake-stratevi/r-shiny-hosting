import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { App } from '../api/types'
import { StatusBadge } from '../components/StatusBadge'
import { EmptyState, ErrorState, Loading, PageHeading, Panel } from '../components/states'
import { useResource } from '../hooks/useResource'
import { useVisiblePolling } from '../hooks/useVisiblePolling'
import { formatDate, relativeTime } from '../lib/time'

const TH = 'px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-faint'
const TD = 'px-4 py-3 align-middle text-sm text-muted'

export function AdminAppsPage() {
  const { data, error, loading, reload } = useResource('apps', (signal) => api.apps(signal))

  useVisiblePolling(() => reload({ quiet: true }), 15_000)

  if (loading) return <Loading label="Loading apps" />
  if (error) return <ErrorState error={error} onRetry={() => reload()} />

  const apps = [...(data ?? [])].sort((a, b) => a.host.localeCompare(b.host))

  return (
    <>
      <PageHeading
        title="Apps"
        subtitle="Every app in the registry. Live state refreshes every 15 seconds."
      />

      {apps.length === 0 ? (
        <EmptyState title="The registry is empty">
          <p>No rows in <code className="font-mono text-xs">shiny-proxy-apps</code> yet.</p>
        </EmptyState>
      ) : (
        <Panel className="overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[52rem] border-collapse">
              <thead className="border-b border-line bg-canvas/60">
                <tr>
                  <th className={TH}>App</th>
                  <th className={TH}>State</th>
                  <th className={TH}>Access</th>
                  <th className={TH}>Expires</th>
                  <th className={TH}>Last active</th>
                  <th className={`${TH} text-right`}>Tasks</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line-soft">
                {apps.map((app) => (
                  <Row key={app.host} app={app} />
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
    </>
  )
}

function Row({ app }: { app: App }) {
  const expiresSoon =
    app.expires_at !== null && app.expires_at * 1000 - Date.now() < 7 * 86_400_000

  return (
    <tr className="transition-colors hover:bg-canvas/70">
      <td className={`${TD} max-w-sm`}>
        <Link
          to={`/admin/apps/${encodeURIComponent(app.host)}`}
          className="font-medium text-ink hover:text-accent hover:underline"
        >
          {app.label || app.app_key || app.host}
        </Link>
        <div className="mt-0.5 truncate font-mono text-xs text-faint">{app.host}</div>
      </td>

      <td className={TD}>
        <StatusBadge state={app.live_state} />
        {app.status !== 'active' && app.status !== app.live_state ? (
          <span className="ml-2 text-xs text-faint">({app.status})</span>
        ) : null}
      </td>

      <td className={TD}>
        {app.access_mode === 'all_users' ? (
          <span>All signed-in users</span>
        ) : app.access_mode === 'users' ? (
          <span>
            {app.allowed_emails.length}{' '}
            {app.allowed_emails.length === 1 ? 'address' : 'addresses'}
          </span>
        ) : (
          <span className="text-amber-800">{app.access_mode} (reserved)</span>
        )}
      </td>

      <td className={TD}>
        {app.expires_at === null ? (
          <span className="text-faint">Never</span>
        ) : (
          <span className={expiresSoon ? 'font-medium text-red-700' : undefined}>
            {formatDate(app.expires_at)}
          </span>
        )}
      </td>

      <td className={TD}>
        <span title={app.last_active ? String(app.last_active) : undefined}>
          {relativeTime(app.last_active)}
        </span>
      </td>

      <td className={`${TD} text-right font-mono text-xs`}>
        {app.running_count}/{app.desired_count}
      </td>
    </tr>
  )
}
