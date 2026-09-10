import { describe, expect, it } from 'vitest'
import type { App } from '../api/types'
import { matchesQuery, matchesStatus, sortApps } from './useAppCollection'

function app(overrides: Partial<App> = {}): App {
  return {
    host: 'dashboard.tools.stratevi.com',
    app_key: 'dashboard',
    label: 'Treatment Pathway Dashboard',
    description: 'Sankey of treatment sequences.',
    ecs_service: 'shiny-dashboard',
    container_port: 3838,
    status: 'active',
    live_state: 'awake',
    access_mode: 'users',
    allowed_emails: ['jake@stratevi.com'],
    idle_minutes: 15,
    max_session_hours: 12,
    expires_at: null,
    last_active: null,
    awake_since: null,
    desired_count: 1,
    running_count: 1,
    ...overrides,
  }
}

describe('matchesQuery', () => {
  const row = app()

  it('matches label, host, key and description, case-insensitively', () => {
    expect(matchesQuery(row, 'pathway')).toBe(true)
    expect(matchesQuery(row, 'DASHBOARD.TOOLS')).toBe(true)
    expect(matchesQuery(row, 'sankey')).toBe(true)
    expect(matchesQuery(row, 'microsimulation')).toBe(false)
  })

  it('treats an empty or whitespace query as no filter', () => {
    expect(matchesQuery(row, '')).toBe(true)
    expect(matchesQuery(row, '   ')).toBe(true)
  })
})

describe('matchesStatus', () => {
  it('groups starting with awake — both are costing money', () => {
    expect(matchesStatus(app({ live_state: 'starting' }), 'awake')).toBe(true)
    expect(matchesStatus(app({ live_state: 'asleep' }), 'awake')).toBe(false)
  })

  it('collects the rows that need a human', () => {
    expect(matchesStatus(app({ allowed_emails: [] }), 'attention')).toBe(true)
    expect(matchesStatus(app(), 'attention')).toBe(false)
  })

  it('puts disabled and expired in one bucket', () => {
    expect(matchesStatus(app({ status: 'disabled' }), 'disabled')).toBe(true)
    expect(matchesStatus(app({ status: 'expired' }), 'disabled')).toBe(true)
    expect(matchesStatus(app(), 'disabled')).toBe(false)
  })
})

describe('sortApps', () => {
  const a = app({ host: 'a', label: 'Beta', last_active: 100, expires_at: 5000 })
  const b = app({ host: 'b', label: 'alpha', last_active: null, expires_at: null })
  const c = app({ host: 'c', label: 'Gamma', last_active: 900, expires_at: 1000 })

  it('sorts by name without case surprises', () => {
    expect(sortApps([a, b, c], 'name').map((x) => x.host)).toEqual(['b', 'a', 'c'])
  })

  it('sinks never-opened apps when sorting by last active', () => {
    expect(sortApps([a, b, c], 'recent').map((x) => x.host)).toEqual(['c', 'a', 'b'])
  })

  it('puts the soonest expiry first and "never" last', () => {
    expect(sortApps([a, b, c], 'expiring').map((x) => x.host)).toEqual(['c', 'a', 'b'])
  })

  it('does not mutate the input', () => {
    const input = [a, b, c]
    sortApps(input, 'name')
    expect(input.map((x) => x.host)).toEqual(['a', 'b', 'c'])
  })
})
