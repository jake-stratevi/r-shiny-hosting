import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { App } from '../api/types'
import { ButtonRoute } from '../components/Button'
import { LinkField } from '../components/LinkField'
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
              iconClass="bg-emerald-50 dark:bg-emerald-400/10 text-emerald-700 dark:text-emerald-300"
              pressed={collection.status === 'awake'}
              onClick={() =>
                collection.setStatus(collection.status === 'awake' ? 'all' : 'awake')
              }
            />
            <StatTile
              label="All apps"
              value={apps.length}
              icon={<GridIcon className="h-4 w-4" />}
              iconClass="bg-azure/10 text-azure"
              pressed={collection.status === 'all'}
              onClick={() => collection.setStatus('all')}
            />
            <StatTile
              label="Needs attention"
              value={attention}
              icon={<InfoIcon className="h-4 w-4" />}
              iconClass={
                attention > 0 ? 'bg-amber-50 dark:bg-amber-400/10 text-amber-700 dark:text-amber-300' : 'bg-background text-muted-foreground/70'
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
            <div className="rounded-lg border border-dashed border-border px-4 py-12 text-center text-sm text-muted-foreground">
              No apps match.{' '}
              <button
                type="button"
                onClick={collection.reset}
                className="text-azure underline underline-offset-2"
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

          <p className="text-xs text-muted-foreground/70">
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
        <SearchIcon className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground/70" />
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
        className="flex rounded-md border border-border bg-card p-0.5"
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
              view === value ? 'bg-accent text-accent-foreground' : 'text-muted-foreground/70 hover:text-foreground'
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
  'rounded-md border border-border bg-card px-3 py-2 text-sm text-foreground outline-none transition-colors focus:border-azure'

function AttentionFlag({ app }: { app: App }) {
  const flag = attentionFlag(app)
  if (!flag) return null
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 dark:bg-amber-400/10 px-2 py-0.5 text-[11px] font-medium text-amber-800 dark:text-amber-300 ring-1 ring-inset ring-amber-600/20 dark:ring-amber-400/30">
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
        className="group flex h-full flex-col rounded-lg border border-border bg-card p-4 shadow-sm transition-all hover:-translate-y-px hover:border-azure/40 hover:shadow-md"
      >
        <div className="flex items-start gap-3">
          <Monogram
            name={label}
            seed={app.host}
            className="h-10 w-14 shrink-0 rounded-md border border-border/60"
            textClassName="text-sm"
          />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold text-foreground group-hover:text-azure">
              {label}
            </p>
            <p className="truncate font-mono text-xs text-muted-foreground/70">{app.host}</p>
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
      <dt className="text-[11px] uppercase tracking-wide text-muted-foreground/70">{term}</dt>
      <dd
        className={`truncate ${mono ? 'font-mono' : ''} ${
          tone === 'warn' ? 'font-medium text-amber-800 dark:text-amber-300' : 'text-muted-foreground'
        }`}
        title={value}
      >
        {value}
      </dd>
    </div>
  )
}

/**
 * List view, on assembled.work's `app-list-grid` template (index.css): the
 * header strip and every row share one grid definition, so the columns line
 * up instead of each row negotiating its own widths. Columns drop from the
 * right as the viewport narrows — app, then link, then status and expires,
 * then last active, then tasks — and the app itself never goes.
 *
 * A grid, not a `<table>`: the shared template is the whole point and a
 * table cannot participate in it, so the roles are stated explicitly and
 * the header strip is a real row rather than decoration.
 */
function AppTable({ apps }: { apps: App[] }) {
  const navigate = useNavigate()

  return (
    <Panel className="overflow-hidden">
      <div role="table" aria-label="Apps">
        <div
          role="row"
          className="app-list-grid gap-3 border-b border-border bg-muted/40 px-3 py-2 text-xs font-medium text-muted-foreground"
        >
          <span role="columnheader">App</span>
          <span role="columnheader" className="hidden md:block">
            Link
          </span>
          <span role="columnheader" className="hidden lg:block">
            State
          </span>
          <span role="columnheader" className="hidden lg:block">
            Expires
          </span>
          <span role="columnheader" className="hidden xl:block">
            Last active
          </span>
          <span role="columnheader" className="hidden 2xl:block">
            Tasks
          </span>
        </div>

        <div className="divide-y divide-border/60">
          {apps.map((app) => (
            <AppListRow key={app.host} app={app} navigate={navigate} />
          ))}
        </div>
      </div>
    </Panel>
  )
}

/** One row of the list view; the columns come from the shared template. */
function AppListRow({
  app,
  navigate,
}: {
  app: App
  navigate: ReturnType<typeof useNavigate>
}) {
  const expiry = expirySummary(app.expires_at)
  const label = app.label || app.app_key || app.host

  return (
    <div
      role="row"
      onClick={(event) => {
        // Let a real click on a link or button (or a modified click) win.
        if (event.defaultPrevented || event.metaKey || event.ctrlKey) return
        navigate(detailPath(app.host, app.live_state))
      }}
      className="app-list-grid cursor-pointer items-center gap-3 px-3 py-2.5 transition-colors hover:bg-accent/40"
    >
      <div role="cell" className="flex min-w-0 items-center gap-3">
        <Monogram
          name={label}
          seed={app.host}
          className="h-9 w-14 shrink-0 rounded-md border border-border/60"
          textClassName="text-xs"
        />
        <div className="min-w-0">
          <Link
            to={detailPath(app.host, app.live_state)}
            className="block truncate text-sm font-medium text-foreground hover:text-azure hover:underline"
          >
            {label}
          </Link>
          <div className="truncate text-xs text-muted-foreground">{accessSummary(app)}</div>
        </div>
      </div>

      <div role="cell" className="hidden min-w-0 md:block">
        <LinkField
          url={`https://${app.host}`}
          kind={app.live_state === 'awake' ? 'live' : 'idle'}
        />
      </div>

      <div role="cell" className="hidden justify-self-start lg:block">
        <div className="flex flex-wrap items-center gap-1.5">
          <StatusBadge state={app.live_state} />
          <AttentionFlag app={app} />
        </div>
      </div>

      <span
        role="cell"
        title={app.expires_at ? formatDate(app.expires_at) : 'No expiry set'}
        className={`hidden whitespace-nowrap text-xs lg:block ${
          expiry.tone === 'soon' || expiry.tone === 'past'
            ? 'font-medium text-amber-800 dark:text-amber-300'
            : 'text-muted-foreground'
        }`}
      >
        {expiry.text}
      </span>

      <span
        role="cell"
        title={app.last_active ? formatDateTime(app.last_active) : undefined}
        className="hidden whitespace-nowrap text-xs text-muted-foreground xl:block"
      >
        {relativeTime(app.last_active)}
      </span>

      <span
        role="cell"
        className="hidden whitespace-nowrap font-mono text-xs text-muted-foreground 2xl:block"
      >
        {app.running_count}/{app.desired_count}
      </span>
    </div>
  )
}
