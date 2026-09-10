// The single source of fake data for mock mode (`npm run dev:mock`) and for
// any test that wants realistic shapes. Every object here must match
// docs/design/portal-api.md exactly -- if a field is wrong, mock mode lies.

import type { App, AuditEvent, Me } from './types'

const NOW = Math.floor(Date.now() / 1000)
const MINUTE = 60
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

export const fixtureMe: Me = {
  email: 'jake@stratevi.com',
  is_admin: true,
}

/** Flip to exercise the non-admin path: `is_admin: false`. */
export const fixtureNonAdminMe: Me = {
  email: 'client@example.com',
  is_admin: false,
}

export const fixtureApps: App[] = [
  {
    host: 'dashboard.tools.stratevi.com',
    app_key: 'dashboard',
    label: 'Treatment Pathway Dashboard',
    description:
      'Sankey of treatment sequences across the Tarpeyo claims cohort, with filters for line of therapy and payer channel.',
    ecs_service: 'shiny-dashboard',
    container_port: 3838,
    status: 'active',
    live_state: 'awake',
    access_mode: 'users',
    allowed_emails: [
      'jake@stratevi.com',
      'nick@stratevi.com',
      'yi@stratevi.com',
      'josh@stratevi.com',
    ],
    idle_minutes: 15,
    max_session_hours: 12,
    expires_at: null,
    last_active: NOW - 3 * MINUTE,
    awake_since: NOW - 42 * MINUTE,
    desired_count: 1,
    running_count: 1,
  },
  {
    host: 'model.tools.stratevi.com',
    app_key: 'model',
    label: 'Microsimulation Model',
    description:
      'Patient-level microsimulation. Sized at 4 vCPU / 16 GB; a run finishes faster and costs about the same.',
    ecs_service: 'shiny-model',
    container_port: 3838,
    status: 'active',
    live_state: 'starting',
    access_mode: 'users',
    allowed_emails: ['jake@stratevi.com', 'yi@stratevi.com'],
    idle_minutes: 20,
    max_session_hours: 12,
    expires_at: NOW + 45 * DAY,
    last_active: NOW - 30,
    awake_since: null,
    desired_count: 1,
    running_count: 0,
  },
  {
    host: 'payer-survey.tools.stratevi.com',
    app_key: 'payer-survey',
    label: 'Payer Survey Explorer',
    description: 'Cross-tabs of the Q2 payer survey. Shared with the client team.',
    ecs_service: 'shiny-payer-survey',
    container_port: 3838,
    status: 'active',
    live_state: 'asleep',
    access_mode: 'all_users',
    allowed_emails: [],
    idle_minutes: 15,
    max_session_hours: 4,
    expires_at: NOW + 6 * DAY,
    last_active: NOW - 2 * DAY,
    awake_since: null,
    desired_count: 0,
    running_count: 0,
  },
  {
    host: 'legacy-forecast.tools.stratevi.com',
    app_key: 'legacy-forecast',
    label: 'Forecast (2025 archive)',
    description: 'Superseded by the microsimulation model. Kept for reference only.',
    ecs_service: 'shiny-legacy-forecast',
    container_port: 3838,
    status: 'disabled',
    live_state: 'disabled',
    access_mode: 'users',
    allowed_emails: ['jake@stratevi.com'],
    idle_minutes: 15,
    max_session_hours: 12,
    expires_at: null,
    last_active: NOW - 96 * DAY,
    awake_since: null,
    desired_count: 0,
    running_count: 0,
  },
  {
    host: 'pilot-uptake.tools.stratevi.com',
    app_key: 'pilot-uptake',
    label: 'Uptake Pilot',
    description: 'Two-week client pilot. Expired on schedule; extend to revive.',
    ecs_service: 'shiny-pilot-uptake',
    container_port: 3838,
    status: 'expired',
    live_state: 'expired',
    access_mode: 'users',
    allowed_emails: ['reviewer@client-example.com'],
    idle_minutes: 15,
    max_session_hours: 8,
    expires_at: NOW - 4 * DAY,
    last_active: NOW - 5 * DAY,
    awake_since: null,
    desired_count: 0,
    running_count: 0,
  },
]

/** Audit log per host, newest first, long enough to exercise "load more". */
export const fixtureAudit: Record<string, AuditEvent[]> = {
  'dashboard.tools.stratevi.com': buildAudit('dashboard.tools.stratevi.com'),
  'model.tools.stratevi.com': buildAudit('model.tools.stratevi.com'),
  'payer-survey.tools.stratevi.com': buildAudit('payer-survey.tools.stratevi.com'),
  'legacy-forecast.tools.stratevi.com': buildAudit('legacy-forecast.tools.stratevi.com'),
  'pilot-uptake.tools.stratevi.com': buildAudit('pilot-uptake.tools.stratevi.com'),
}

function buildAudit(host: string): AuditEvent[] {
  const emails = [
    'jake@stratevi.com',
    'nick@stratevi.com',
    'yi@stratevi.com',
    'reviewer@client-example.com',
  ]
  const kinds: Array<[string, string]> = [
    ['allow', '/'],
    ['allow', '/session/abc123/'],
    ['wake', '/'],
    ['deny', '/'],
    ['config_change', 'allowed_emails,idle_minutes'],
    ['sleep', '/'],
  ]
  const seed = host.length
  const out: AuditEvent[] = []
  for (let i = 0; i < 137; i++) {
    const [event, path] = kinds[(i + seed) % kinds.length]
    out.push({
      event,
      email: emails[(i * 3 + seed) % emails.length],
      path,
      ts: NOW - i * 37 * MINUTE,
    })
  }
  return out
}
