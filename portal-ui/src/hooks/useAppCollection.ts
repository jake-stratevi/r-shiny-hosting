import { useMemo, useState } from 'react'
import type { App } from '../api/types'
import { needsAttention } from '../lib/appDisplay'

/**
 * The browse block behind the admin list, ported from assembled.work's
 * `useAppCollection`: search, a status filter, a sort, and a card/table
 * toggle the browser remembers. Their version also filters by team; we have
 * no teams, so that control is gone rather than stubbed.
 */

export type StatusFilter = 'all' | 'awake' | 'asleep' | 'attention' | 'disabled'
export type SortKey = 'name' | 'recent' | 'expiring'
export type CollectionView = 'grid' | 'table'

export const STATUS_FILTERS: ReadonlyArray<{ value: StatusFilter; label: string }> = [
  { value: 'all', label: 'All apps' },
  { value: 'awake', label: 'Awake' },
  { value: 'asleep', label: 'Asleep' },
  { value: 'attention', label: 'Needs attention' },
  { value: 'disabled', label: 'Disabled or expired' },
]

export const SORT_OPTIONS: ReadonlyArray<{ value: SortKey; label: string }> = [
  { value: 'name', label: 'Sort: name' },
  { value: 'recent', label: 'Sort: last active' },
  { value: 'expiring', label: 'Sort: expiring first' },
]

const VIEW_KEY = 'portalAdminView'

function storedView(): CollectionView {
  try {
    return localStorage.getItem(VIEW_KEY) === 'grid' ? 'grid' : 'table'
  } catch {
    return 'table'
  }
}

export function matchesQuery(app: App, query: string): boolean {
  const q = query.trim().toLowerCase()
  if (q === '') return true
  return [app.label, app.host, app.app_key, app.description]
    .filter(Boolean)
    .some((field) => String(field).toLowerCase().includes(q))
}

export function matchesStatus(app: App, filter: StatusFilter): boolean {
  switch (filter) {
    case 'all':
      return true
    case 'awake':
      return app.live_state === 'awake' || app.live_state === 'starting'
    case 'asleep':
      return app.live_state === 'asleep'
    case 'attention':
      return needsAttention(app) !== null
    case 'disabled':
      return app.status === 'disabled' || app.status === 'expired'
  }
}

export function sortApps(apps: App[], key: SortKey): App[] {
  const out = [...apps]
  switch (key) {
    case 'name':
      return out.sort((a, b) =>
        (a.label || a.host).localeCompare(b.label || b.host, undefined, {
          sensitivity: 'base',
        }),
      )
    case 'recent':
      // Never-opened apps sink, rather than sorting as if they were ancient.
      return out.sort((a, b) => (b.last_active ?? -1) - (a.last_active ?? -1))
    case 'expiring':
      // "Never" is not urgent, so it goes last however the numbers compare.
      return out.sort(
        (a, b) => (a.expires_at ?? Number.POSITIVE_INFINITY) - (b.expires_at ?? Number.POSITIVE_INFINITY),
      )
  }
}

export function useAppCollection(apps: App[]) {
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState<StatusFilter>('all')
  const [sort, setSort] = useState<SortKey>('name')
  const [view, setViewState] = useState<CollectionView>(storedView)

  function setView(next: CollectionView) {
    setViewState(next)
    try {
      localStorage.setItem(VIEW_KEY, next)
    } catch {
      /* private mode: the preference just doesn't stick */
    }
  }

  const results = useMemo(
    () => sortApps(apps.filter((a) => matchesQuery(a, query) && matchesStatus(a, status)), sort),
    [apps, query, status, sort],
  )

  const filtered = query.trim() !== '' || status !== 'all'

  function reset() {
    setQuery('')
    setStatus('all')
  }

  return {
    query,
    setQuery,
    status,
    setStatus,
    sort,
    setSort,
    view,
    setView,
    results,
    filtered,
    reset,
  }
}
