import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { App } from '../api/types'
import { ButtonRoute } from '../components/Button'
import { Monogram } from '../components/Monogram'
import { StatTile } from '../components/StatTile'
import { StatusBadge } from '../components/StatusBadge'
import { EmptyState, ErrorState, Loading, PageHeader, Panel } from '../components/states'
import { BoltIcon, GridIcon, InfoIcon, ListIcon, PlusIcon, SearchIcon } from '../components/icons'
import {
  SORT_OPTIONS,
  STATUS_FILTERS,
  useAppCollection,
  type CollectionView,
  type SortKey,
  type StatusFilter,
} from '../hooks/useAppCollection'
import { useResource } from '../hooks/useResource'
import { useVisiblePolling } from '../hooks/useVisiblePolling'
import { accessSummary, attentionFlag, expirySummary, needsAttention } from '../lib/appDisplay'
import { useMe } from '../lib/meContext'
import { formatDate, formatDateTime, relativeTime } from '../lib/time'

/**
 * An app mid-build has no settings worth editing yet, so its row points at
 * the build screen instead of the detail page.
 */
const detailPath = (host: string, liveState?: string) =>
  liveState === 'building'
    ? `/admin/apps/${encodeURIComponent(host)}/build`
    : `/admin/apps/${encodeURIComponent(host)}`

export function AdminAppsPage() {
  const me = useMe()
  const { data, error, loading, reload } = useResource('apps', (signal) => api.apps(signal))

  useVisiblePolling(() => reload({ quiet: true }), 15_000)

  const apps = data ?? []
  const collection = useAppCollection(apps)

  if (loading) return <Loading label="Loading apps" />
  if (error) return <ErrorState error={error} onRetry={() => reload()} />

  const awake = apps.filter((a) => a.live_state === 'awake' || a.live_state === 'starting').length
  const attention = apps.filter((a) => needsAttention(a) !== null).length

  return (
    <>
      <PageHeader
        title="Apps"
        description="Every app in the registry, and who can open it."
        meta="Live state refreshes every 15 seconds while this tab is visible."
        actions={
          // Creation is its own permission: an admin without it never sees
          // this, rather than being dangled a 403 (portal-p2a.md Decisions).
          me?.can_create ? (
            <ButtonRoute to="/admin/apps/new" variant="outline">
              <PlusIcon className="h-4 w-4" />
              New app
            </ButtonRoute>
          ) : null
        }
      />

      {apps.length === 0 ? (
        <EmptyState title="The registry is empty">
          <p>
            No rows in <code className="font-mono text-xs">shiny-proxy-apps</code> yet.
          </p>
        </EmptyState>
      ) : (
        <div className="space-y-5">
          {/* Every number is a door: clicking a tile filters the list below. */}
          <div className="grid gap-3 sm:max-w-2xl sm:grid-cols-3">
            <StatTile
              label="Awake now"
              value={awake}
              icon={<BoltIcon className="h-4 w-4" />}
              iconClass="bg-emerald-50 text-emerald-700"
              pressed={collection.status === 'awake'}
              onClick={() =>
                collection.setStatus(collection.status === 'awake' ? 'all' : 'awake')
              }
            />
            <StatTile
              label="All apps"
              value={apps.length}
              icon={<GridIcon className="h-4 w-4" />}
              iconClass="bg-accent-soft text-accent"
              pressed={collection.status === 'all'}
              onClick={() => collection.setStatus('all')}
            />
            <StatTile
              label="Needs attention"
              value={attention}
              icon={<InfoIcon className="h-4 w-4" />}
              iconClass={
                attention > 0 ? 'bg-amber-50 text-amber-700' : 'bg-canvas text-faint'
              }
              pressed={collection.status === 'attention'}
              onClick={() =>
                collection.setStatus(collection.status === 'attention' ? 'all' : 'attention')
              }
            />
          </div>

          <BrowseBar
            query={collection.query}
            onQuery={collection.setQuery}
            status={collection.status}
            onStatus={collection.setStatus}
            sort={collection.sort}
            onSort={collection.setSort}
            view={collection.view}
            onView={collection.setView}
          />

          {collection.results.length === 0 ? (
            <div className="rounded-card border border-dashed border-line px-4 py-12 text-center text-sm text-muted">
              No apps match.{' '}
              <button
                type="button"
                onClick={collection.reset}
                className="text-accent underline underline-offset-2"
              >
                Clear search and filters
              </button>
            </div>
          ) : collection.view === 'grid' ? (
            <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {collection.results.map((app) => (
                <AppCard key={app.host} app={app} />
              ))}
            </ul>
          ) : (
            <AppTable apps={collection.results} />
          )}

          <p className="text-xs text-faint">
            Showing {collection.results.length} of {apps.length}{' '}
            {apps.length === 1 ? 'app' : 'apps'}.
          </p>
        </div>
      )}
    </>
  )
}

/** Search, filter, sort and a remembered card/table toggle. */
function BrowseBar({
  query,
  onQuery,
  status,
  onStatus,
  sort,
  onSort,
  view,
  onView,
}: {
  query: string
  onQuery: (v: string) => void
  status: StatusFilter
  onStatus: (v: StatusFilter) => void
  sort: SortKey
  onSort: (v: SortKey) => void
  view: CollectionView
  onView: (v: CollectionView) => void
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <div className="relative min-w-[12rem] flex-1">
        <SearchIcon className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-faint" />
        <input
          type="search"
          value={query}
          aria-label="Search apps"
          placeholder="Search by name, host or key"
          onChange={(e) => onQuery(e.target.value)}
          className={`${selectClass} w-full pl-8`}
        />
      </div>

      <select
        value={status}
        aria-label="Filter by status"
        onChange={(e) => onStatus(e.target.value as StatusFilter)}
        className={selectClass}
      >
        {STATUS_FILTERS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>

      <select
        value={sort}
        aria-label="Sort apps"
        onChange={(e) => onSort(e.target.value as SortKey)}
        className={selectClass}
      >
        {SORT_OPTIONS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>

      <div
        role="group"
        aria-label="View"
        className="flex rounded-tile border border-line bg-surface p-0.5"
      >
        {(
          [
            ['table', 'Table view', <ListIcon key="l" className="h-4 w-4" />],
            ['grid', 'Card view', <GridIcon key="g" className="h-4 w-4" />],
          ] as const
        ).map(([value, label, icon]) => (
          <button
            key={value}
            type="button"
            aria-label={label}
            aria-pressed={view === value}
            onClick={() => onView(value)}
            className={`rounded-[6px] px-2 py-1.5 transition-colors ${
              view === value ? 'bg-accent-soft text-accent' : 'text-faint hover:text-ink'
            }`}
          >
            {icon}
          </button>
        ))}
      </div>
    </div>
  )
}

const selectClass =
  'rounded-tile border border-line bg-surface px-3 py-2 text-sm text-ink outline-none transition-colors focus:border-accent'

function AttentionFlag({ app }: { app: App }) {
  const flag = attentionFlag(app)
  if (!flag) return null
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-800 ring-1 ring-inset ring-amber-600/20">
      <InfoIcon className="h-3 w-3" />
      {flag}
    </span>
  )
}

/** Card view — the same facts as a row, arranged for scanning. */
function AppCard({ app }: { app: App }) {
  const expiry = expirySummary(app.expires_at)
  const label = app.label || app.app_key || app.host

  return (
    // min-w-0: without it the grid track grows to fit the unbreakable host
    // string and the card overflows the viewport on a phone.
    <li className="min-w-0">
      <Link
        to={detailPath(app.host, app.live_state)}
        className="group flex h-full flex-col rounded-card border border-line bg-surface p-4 shadow-card transition-all hover:-translate-y-px hover:border-accent-line hover:shadow-card-hover"
      >
        <div className="flex items-start gap-3">
          <Monogram
            name={label}
            seed={app.host}
            className="h-10 w-14 shrink-0 rounded-tile border border-line-soft"
            textClassName="text-sm"
          />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold text-ink group-hover:text-accent">
              {label}
            </p>
            <p className="truncate font-mono text-xs text-faint">{app.host}</p>
          </div>
          <StatusBadge state={app.live_state} />
        </div>

        <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-2.5 text-xs">
          <Fact term="Access" value={accessSummary(app)} />
          <Fact
            term="Expires"
            value={expiry.text}
            tone={expiry.tone === 'soon' || expiry.tone === 'past' ? 'warn' : undefined}
          />
          <Fact term="Last active" value={relativeTime(app.last_active)} />
          <Fact term="Tasks" value={`${app.running_count}/${app.desired_count}`} mono />
        </dl>

        <div className="mt-auto pt-3">
          <AttentionFlag app={app} />
        </div>
      </Link>
    </li>
  )
}

function Fact({
  term,
  value,
  tone,
  mono,
}: {
  term: string
  value: string
  tone?: 'warn'
  mono?: boolean
}) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] uppercase tracking-wide text-faint">{term}</dt>
      <dd
        className={`truncate ${mono ? 'font-mono' : ''} ${
          tone === 'warn' ? 'font-medium text-amber-800' : 'text-muted'
        }`}
        title={value}
      >
        {value}
      </dd>
    </div>
  )
}

/**
 * Table view. Real table semantics, but the whole row is a click target the
 * way assembled.work's list rows are — the label stays a proper link so
 * keyboards and middle-clicks still work.
 */
function AppTable({ apps }: { apps: App[] }) {
  const navigate = useNavigate()

  return (
    <Panel className="overflow-hidden">
      <div className="overflow-x-auto">
        {/* Columns drop from the right as the viewport narrows, the way
            assembled.work's list grid does; nothing important is ever the
            first to go. */}
        <table className="w-full min-w-[34rem] border-collapse">
          <thead className="border-b border-line bg-canvas/60">
            <tr>
              <th className={TH}>App</th>
              <th className={TH}>State</th>
              <th className={`${TH} hidden md:table-cell`}>Access</th>
              <th className={`${TH} hidden md:table-cell`}>Expires</th>
              <th className={`${TH} hidden lg:table-cell`}>Last active</th>
              <th className={`${TH} hidden text-right lg:table-cell`}>Tasks</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line-soft">
            {apps.map((app) => {
              const expiry = expirySummary(app.expires_at)
              const label = app.label || app.app_key || app.host
              return (
                <tr
                  key={app.host}
                  onClick={(event) => {
                    // Let a real click on the link (or a modified click) win.
                    if (event.defaultPrevented || event.metaKey || event.ctrlKey) return
                    navigate(detailPath(app.host, app.live_state))
                  }}
                  className="cursor-pointer transition-colors hover:bg-accent-soft/40"
                >
                  <td className={`${TD} max-w-sm`}>
                    <div className="flex items-center gap-3">
                      <Monogram
                        name={label}
                        seed={app.host}
                        className="h-9 w-12 shrink-0 rounded-tile border border-line-soft"
                        textClassName="text-xs"
                      />
                      <div className="min-w-0">
                        <Link
                          to={detailPath(app.host, app.live_state)}
                          className="block truncate font-medium text-ink hover:text-accent hover:underline"
                        >
                          {label}
                        </Link>
                        <div className="truncate font-mono text-xs text-faint">{app.host}</div>
                      </div>
                    </div>
                  </td>

                  <td className={TD}>
                    <div className="flex flex-wrap items-center gap-1.5">
                      <StatusBadge state={app.live_state} />
                      <AttentionFlag app={app} />
                    </div>
                  </td>

                  <td className={`${TD} hidden md:table-cell`}>{accessSummary(app)}</td>

                  <td className={`${TD} hidden md:table-cell`}>
                    <span
                      title={app.expires_at ? formatDate(app.expires_at) : 'No expiry set'}
                      className={
                        expiry.tone === 'soon' || expiry.tone === 'past'
                          ? 'font-medium text-amber-800'
                          : expiry.tone === 'never'
                            ? 'text-faint'
                            : undefined
                      }
                    >
                      {expiry.text}
                    </span>
                  </td>

                  <td className={`${TD} hidden lg:table-cell`}>
                    <span title={app.last_active ? formatDateTime(app.last_active) : undefined}>
                      {relativeTime(app.last_active)}
                    </span>
                  </td>

                  <td className={`${TD} hidden text-right font-mono text-xs lg:table-cell`}>
                    {app.running_count}/{app.desired_count}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}

const TH = 'px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-faint'
const TD = 'px-4 py-3 align-middle text-sm text-muted'
