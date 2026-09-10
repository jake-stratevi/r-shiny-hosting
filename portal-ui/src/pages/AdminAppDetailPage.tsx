import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import type { App } from '../api/types'
import { StatusBadge } from '../components/StatusBadge'
import { ErrorState, Loading, Panel } from '../components/states'
import { useResource } from '../hooks/useResource'
import { formatDateTime, relativeTime } from '../lib/time'
import { AppSettingsForm } from './AppSettingsForm'
import { AuditLog } from './AuditLog'

type Tab = 'settings' | 'audit'

export function AdminAppDetailPage() {
  const { host = '' } = useParams<{ host: string }>()
  const [params, setParams] = useSearchParams()
  const tab: Tab = params.get('tab') === 'audit' ? 'audit' : 'settings'

  const { data, error, loading, reload } = useResource(`app:${host}`, (signal) =>
    api.app(host, signal),
  )

  // Held locally so a PATCH response updates the page without a refetch.
  const [app, setApp] = useState<App | null>(null)
  useEffect(() => setApp(data), [data])

  if (loading) return <Loading label="Loading app" />
  if (error) return <ErrorState error={error} onRetry={() => reload()} />
  if (!app) return null

  return (
    <>
      <nav className="mb-5 text-sm">
        <Link to="/admin" className="text-muted hover:text-accent hover:underline">
          ← All apps
        </Link>
      </nav>

      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-semibold tracking-tight text-ink">
              {app.label || app.host}
            </h1>
            <StatusBadge state={app.live_state} />
          </div>
          <a
            href={`https://${app.host}`}
            className="mt-1.5 inline-block font-mono text-xs text-faint hover:text-accent hover:underline"
          >
            {app.host}
          </a>
        </div>
      </div>

      <Facts app={app} />

      <div className="mb-6 mt-8 flex gap-1 border-b border-line">
        {(['settings', 'audit'] as const).map((value) => (
          <button
            key={value}
            type="button"
            onClick={() => setParams(value === 'settings' ? {} : { tab: value }, { replace: true })}
            className={`-mb-px rounded-t-md border-b-2 px-4 py-2 text-sm font-medium capitalize transition-colors ${
              tab === value
                ? 'border-accent text-accent'
                : 'border-transparent text-muted hover:text-ink'
            }`}
          >
            {value === 'audit' ? 'Audit log' : 'Settings'}
          </button>
        ))}
      </div>

      {tab === 'settings' ? (
        <AppSettingsForm key={app.host} app={app} onSaved={setApp} />
      ) : (
        <AuditLog host={app.host} />
      )}
    </>
  )
}

function Facts({ app }: { app: App }) {
  const items: Array<[string, string]> = [
    ['Key', app.app_key || '—'],
    ['ECS service', app.ecs_service || '—'],
    ['Container port', String(app.container_port ?? '—')],
    ['Tasks (running/desired)', `${app.running_count}/${app.desired_count}`],
    ['Awake since', app.awake_since ? formatDateTime(app.awake_since) : '—'],
    ['Last active', relativeTime(app.last_active)],
  ]

  return (
    <Panel className="grid grid-cols-2 gap-x-8 gap-y-4 px-6 py-5 sm:grid-cols-3 lg:grid-cols-6">
      {items.map(([label, value]) => (
        <div key={label}>
          <div className="text-xs uppercase tracking-wide text-faint">{label}</div>
          <div className="mt-1 truncate font-mono text-sm text-ink" title={value}>
            {value}
          </div>
        </div>
      ))}
    </Panel>
  )
}
