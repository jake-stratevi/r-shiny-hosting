import { useCallback, useEffect, useState } from 'react'
import { ApiError, api } from '../api/client'
import type { AuditEvent } from '../api/types'
import { Button } from '../components/Button'
import { EmptyState, ErrorState, Loading, SectionCard } from '../components/states'
import { formatDateTime, relativeTime } from '../lib/time'

const PAGE_SIZE = 50

/** Colour by kind. Unknown event names get the neutral chip. */
function eventClass(event: string): string {
  switch (event) {
    case 'allow':
      return 'bg-emerald-50 text-emerald-800 ring-emerald-600/20'
    case 'deny':
      return 'bg-red-50 text-red-700 ring-red-600/20'
    case 'wake':
      return 'bg-amber-50 text-amber-800 ring-amber-600/25'
    case 'sleep':
      return 'bg-slate-100 text-slate-600 ring-slate-500/15'
    case 'config_change':
      return 'bg-accent-soft text-accent ring-accent/25'
    default:
      return 'bg-slate-100 text-slate-600 ring-slate-500/20'
  }
}

export function AuditLog({ host }: { host: string }) {
  const [events, setEvents] = useState<AuditEvent[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)

  const load = useCallback(
    async (from: string | null) => {
      const first = from === null
      if (first) setLoading(true)
      else setLoadingMore(true)
      try {
        const page = await api.audit(host, { limit: PAGE_SIZE, cursor: from })
        setEvents((prev) => (first ? page.events : [...prev, ...page.events]))
        setCursor(page.cursor ?? null)
        setError(null)
      } catch (err) {
        setError(err instanceof ApiError ? err : new ApiError(0, String(err)))
      } finally {
        setLoading(false)
        setLoadingMore(false)
      }
    },
    [host],
  )

  useEffect(() => {
    setEvents([])
    setCursor(null)
    void load(null)
  }, [load])

  if (loading) return <Loading label="Loading events" />
  if (error && events.length === 0) {
    return <ErrorState error={error} onRetry={() => void load(null)} />
  }
  if (events.length === 0) {
    return (
      <EmptyState title="No events yet">
        <p>Access decisions, wakes and configuration changes show up here.</p>
      </EmptyState>
    )
  }

  return (
    <div className="space-y-4">
      <SectionCard
        title="Audit log"
        description="Newest first. Written by the proxy on every access decision, wake and config change."
        actions={
          <span className="text-xs text-faint">
            {events.length} {events.length === 1 ? 'event' : 'events'} loaded
          </span>
        }
      >
        <div className="overflow-x-auto">
          <table className="w-full min-w-[40rem] border-collapse">
            <thead className="border-b border-line-soft bg-canvas/60">
              <tr>
                <th className={TH}>Event</th>
                <th className={TH}>Who</th>
                <th className={TH}>Path</th>
                <th className={`${TH} text-right`}>When</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line-soft">
              {events.map((event, index) => (
                <tr key={`${event.ts}-${index}`} className="hover:bg-canvas/70">
                  <td className="px-4 py-2.5">
                    <span
                      className={`inline-flex rounded-full px-2 py-0.5 font-mono text-xs font-medium ring-1 ring-inset ${eventClass(event.event)}`}
                    >
                      {event.event}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-sm text-muted">{event.email || '—'}</td>
                  <td className="max-w-xs truncate px-4 py-2.5 font-mono text-xs text-faint">
                    {event.path || '—'}
                  </td>
                  <td
                    className="whitespace-nowrap px-4 py-2.5 text-right text-sm text-muted"
                    title={formatDateTime(event.ts)}
                  >
                    {relativeTime(event.ts)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </SectionCard>

      {error ? <ErrorState error={error} onRetry={() => void load(cursor)} /> : null}

      <div className="flex items-center gap-3">
        {cursor ? (
          <Button variant="outline" disabled={loadingMore} onClick={() => void load(cursor)}>
            {loadingMore ? 'Loading…' : 'Load more'}
          </Button>
        ) : (
          <span className="text-xs text-faint">End of the log.</span>
        )}
      </div>
    </div>
  )
}

const TH = 'px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-faint'
