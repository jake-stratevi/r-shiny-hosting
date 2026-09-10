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
  // P2a: independent of is_admin. `__portalMock.setCreator(false)` turns it
  // off to see the admin-who-cannot-create view.
  can_create: true,
}

/** Flip to exercise the non-admin path: `is_admin: false`. */
export const fixtureNonAdminMe: Me = {
  email: 'client@example.com',
  is_admin: false,
  can_create: false,
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
  // P2a states, so the amber-animated and red build chips are on screen
  // without having to run the wizard first.
  {
    host: 'access-atlas.tools.stratevi.com',
    app_key: 'access-atlas',
    label: 'Access Atlas',
    description: 'County-level payer coverage map. Created this morning; still building.',
    ecs_service: 'shiny-access-atlas',
    container_port: 3838,
    status: 'building',
    live_state: 'building',
    access_mode: 'users',
    allowed_emails: ['jake@stratevi.com'],
    idle_minutes: 20,
    max_session_hours: 12,
    expires_at: NOW + 90 * DAY,
    last_active: null,
    awake_since: null,
    desired_count: 0,
    running_count: 0,
  },
  {
    host: 'copay-sim.tools.stratevi.com',
    app_key: 'copay-sim',
    label: 'Copay Simulator',
    description: 'First build failed on an rstan install. Row kept for inspection.',
    ecs_service: 'shiny-copay-sim',
    container_port: 3838,
    status: 'build_failed',
    live_state: 'build_failed',
    access_mode: 'users',
    allowed_emails: ['jake@stratevi.com'],
    idle_minutes: 10,
    max_session_hours: 12,
    expires_at: null,
    last_active: null,
    awake_since: null,
    desired_count: 0,
    running_count: 0,
  },
]

// There is no inspection fixture: the wizard reads the real zip the user
// chose, in the browser, so `dev:mock` exercises the real code path.

/**
 * A believable CodeBuild log tail. The lines are ordered the way the phases
 * come out, so the build screen's tail grows plausibly as the mock advances.
 */
export const fixtureBuildLog: string[] = [
  '[Container] Entering phase DOWNLOAD_SOURCE',
  '[Container] Fetching s3://shiny-portal-uploads-652063276768/uploads/…zip',
  '[Container] Bundle: 41 entries, 2.7 MB extracted, entrypoint app.R',
  '[Container] Entering phase PRE_BUILD',
  '[Container] Logging in to 652063276768.dkr.ecr.us-east-1.amazonaws.com',
  '[Container] Entering phase BUILD',
  'Step 3/9 : FROM rocker/r-ver:4.4.1',
  "Step 6/9 : RUN install2.r --error --skipinstalled shiny bslib dplyr tidyr",
  '* installing *source* package ‘dplyr’ ...',
  '** byte-compile and prepare package for lazy loading',
  '* DONE (dplyr)',
  '* installing *source* package ‘ggplot2’ ...',
  '* DONE (ggplot2)',
  '* installing *source* package ‘networkD3’ ...',
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
