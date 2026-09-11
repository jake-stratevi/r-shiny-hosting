// Mock transport for `npm run dev:mock`. Loaded lazily by client.ts and only
// when VITE_PORTAL_MOCK=1, so it costs the production bundle nothing.
//
// It returns real `Response` objects, which means the client's CSRF, JSON and
// error handling run exactly as they do against the live service.

import {
  fixtureApps,
  fixtureAudit,
  fixtureBuildLog,
  fixtureMe,
} from './fixtures'
import {
  ACCESS_MODES,
  IDLE_MINUTES_MAX,
  IDLE_MINUTES_MIN,
  KEY_MAX_LENGTH,
  KEY_MIN_LENGTH,
  MAX_SESSION_HOURS_MAX,
  MAX_SESSION_HOURS_MIN,
  MAX_ZIP_BYTES,
  MAX_ZIP_MB,
  TASK_SIZES,
  type App,
  type AppPatch,
  type LiveState,
} from './types'
import type { UploadOptions } from './client'

const LATENCY_MS = 180

// Mutable copy so PATCHes stick for the life of the page.
const state: App[] = fixtureApps.map((a) => ({ ...a, allowed_emails: [...a.allowed_emails] }))
const ADMIN_KEY = 'portalMockAdmin'
const CREATOR_KEY = 'portalMockCreator'
const STAFF_KEY = 'portalMockStaff'

function storedFlag(key: string): boolean | null {
  try {
    const raw = localStorage.getItem(key)
    if (raw === '0') return false
    if (raw === '1') return true
  } catch {
    /* private mode, whatever */
  }
  return null
}

function rememberFlag(key: string, value: boolean): void {
  try {
    localStorage.setItem(key, value ? '1' : '0')
  } catch {
    /* ignore */
  }
}

let me = {
  ...fixtureMe,
  is_admin: storedFlag(ADMIN_KEY) ?? fixtureMe.is_admin,
  can_create: storedFlag(CREATOR_KEY) ?? fixtureMe.can_create ?? false,
  is_staff: storedFlag(STAFF_KEY) ?? fixtureMe.is_staff ?? false,
}

/**
 * Flip any gate from the console and reload:
 *   __portalMock.setAdmin(false)     the non-admin view + 403 state
 *   __portalMock.setCreator(false)   an admin who may not create
 *   __portalMock.setStaff(false)     the external-client view: Apps only
 */
declare global {
  // eslint-disable-next-line no-var
  var __portalMock:
    | {
        setAdmin(v: boolean): void
        setCreator(v: boolean): void
        setStaff(v: boolean): void
        apps(): App[]
        /** Make the next created app's build fail, to see that screen. */
        failNextBuild(v?: boolean): void
      }
    | undefined
}
globalThis.__portalMock = {
  setAdmin(v: boolean) {
    me = { ...me, is_admin: v }
    rememberFlag(ADMIN_KEY, v)
  },
  setCreator(v: boolean) {
    me = { ...me, can_create: v }
    rememberFlag(CREATOR_KEY, v)
  },
  setStaff(v: boolean) {
    me = { ...me, is_staff: v }
    rememberFlag(STAFF_KEY, v)
  },
  apps: () => state,
  failNextBuild(v = true) {
    failNext = v
  },
}

const PATCHABLE = new Set<keyof AppPatch>([
  'label',
  'description',
  'access_mode',
  'allowed_emails',
  'idle_minutes',
  'max_session_hours',
  'expires_at',
  'status',
])

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const fail = (status: number, error: string) => json({ error }, status)

function visibleToCaller(app: App): boolean {
  if (app.status !== 'active') return false
  if (app.access_mode === 'all_users') return true
  if (app.access_mode === 'users') return app.allowed_emails.includes(me.email.toLowerCase())
  return false // reserved modes fail closed, same as the proxy
}

/** Make `starting` drift to `awake` so the 15s poll visibly does something. */
function drift(): void {
  for (const app of state) {
    if (app.live_state === 'starting' && Math.random() < 0.35) {
      app.live_state = 'awake'
      app.running_count = 1
      app.awake_since = Math.floor(Date.now() / 1000)
    }
  }
}

function deriveLiveState(app: App): LiveState {
  if (app.status === 'building') return 'building'
  if (app.status === 'build_failed') return 'build_failed'
  if (app.status === 'disabled') return 'disabled'
  if (app.status === 'expired') return 'expired'
  if (app.running_count > 0) return 'awake'
  if (app.desired_count > 0) return 'starting'
  return 'asleep'
}

export async function mockFetch(url: string, init: RequestInit = {}): Promise<Response> {
  await new Promise((r) => setTimeout(r, LATENCY_MS))
  drift()

  const method = (init.method ?? 'GET').toUpperCase()
  const { pathname, searchParams } = new URL(url, 'http://mock.local')

  if (method !== 'GET' && (init.headers as Record<string, string>)?.['X-Portal-Csrf'] !== '1') {
    return fail(403, 'Missing CSRF header.')
  }

  if (pathname === '/api/v1/me') return json(me)

  // --- P2a creation routes -------------------------------------------------
  // Deliberately BEFORE the admin gate below: creation is its own permission
  // and admin alone must be a 403, not a pass (portal-p2a.md Decisions).
  if (
    pathname === '/api/v1/apps/validate-key' ||
    pathname === '/api/v1/uploads' ||
    (pathname === '/api/v1/apps' && method === 'POST') ||
    /^\/api\/v1\/apps\/[^/]+\/build$/.test(pathname)
  ) {
    if (!me.can_create) {
      return fail(403, 'Creating apps requires creator permission.')
    }
  }

  if (pathname === '/api/v1/apps/validate-key') {
    if (method !== 'POST') return fail(405, 'Method not allowed.')
    return json(validateKey(String(body(init).key ?? '')))
  }

  if (pathname === '/api/v1/uploads') {
    if (method !== 'POST') return fail(405, 'Method not allowed.')
    const { filename, size } = body(init)
    if (typeof filename !== 'string' || !filename.toLowerCase().endsWith('.zip')) {
      return fail(400, 'Only .zip bundles are accepted.')
    }
    if (Number(size) > MAX_ZIP_BYTES) {
      return fail(400, `That bundle is over the ${MAX_ZIP_MB} MB limit.`)
    }
    return json({
      upload_key: `uploads/${uuid()}.zip`,
      url: 'https://shiny-portal-uploads-652063276768.s3.amazonaws.com/mock-presigned',
      expires_in: 900,
    })
  }

  // No /uploads/inspect: the wizard reads the zip in the browser before it
  // uploads (src/lib/inspectBundle.ts), so there is nothing to mock here.

  if (pathname === '/api/v1/apps' && method === 'POST') return createApp(init)

  const buildMatch = pathname.match(/^\/api\/v1\/apps\/([^/]+)\/build$/)
  if (buildMatch) {
    if (method !== 'GET') return fail(405, 'Method not allowed.')
    return buildStatus(decodeURIComponent(buildMatch[1]))
  }

  if (pathname === '/api/v1/menu') {
    return json({
      apps: state.filter(visibleToCaller).map((a) => ({
        host: a.host,
        label: a.label,
        description: a.description,
        url: `https://${a.host}`,
        live_state: a.live_state,
      })),
    })
  }

  if (pathname.startsWith('/api/v1/apps') || pathname === '/api/v1/costs') {
    if (!me.is_admin) return fail(403, 'Admin access is required.')
  }

  if (pathname === '/api/v1/costs') {
    if (method !== 'GET') return fail(405, 'Method not allowed.')
    const { mockCosts } = await import('./mockCosts')
    return json(mockCosts(state))
  }

  const costsMatch = pathname.match(/^\/api\/v1\/apps\/([^/]+)\/costs$/)
  if (costsMatch) {
    if (method !== 'GET') return fail(405, 'Method not allowed.')
    const host = decodeURIComponent(costsMatch[1])
    const app = state.find((a) => a.host === host)
    if (!app) return fail(404, 'No such application.')
    const { mockCosts } = await import('./mockCosts')
    const report = mockCosts([app])
    return json({
      currency: report.currency,
      basis: report.basis,
      generated_at: report.generated_at,
      rates: report.rates,
      disclaimer: report.disclaimer,
      month_to_date: report.month_to_date.apps[0],
      previous_month: report.previous_month.apps[0],
    })
  }

  if (pathname === '/api/v1/apps') return json({ apps: state })

  const auditMatch = pathname.match(/^\/api\/v1\/apps\/([^/]+)\/audit$/)
  if (auditMatch) {
    const host = decodeURIComponent(auditMatch[1])
    const all = fixtureAudit[host] ?? []
    const limit = Math.min(Number(searchParams.get('limit')) || 50, 200)
    const offset = Number(decodeCursor(searchParams.get('cursor'))) || 0
    const slice = all.slice(offset, offset + limit)
    const next = offset + limit < all.length ? encodeCursor(offset + limit) : null
    return json({ events: slice, cursor: next })
  }

  const appMatch = pathname.match(/^\/api\/v1\/apps\/([^/]+)$/)
  if (appMatch) {
    const host = decodeURIComponent(appMatch[1])
    const app = state.find((a) => a.host === host)
    if (!app) return fail(404, `No app is registered for ${host}.`)
    if (method === 'GET') return json(app)
    if (method === 'PATCH') return applyPatch(app, init)
    return fail(405, 'Method not allowed.')
  }

  return fail(404, `No route for ${pathname}.`)
}

async function applyPatch(app: App, init: RequestInit): Promise<Response> {
  let patch: Record<string, unknown>
  try {
    patch = JSON.parse(String(init.body ?? '{}'))
  } catch {
    return fail(400, 'Body is not valid JSON.')
  }

  for (const key of Object.keys(patch)) {
    if (!PATCHABLE.has(key as keyof AppPatch)) return fail(400, `Unknown field ${key}.`)
  }
  if ('access_mode' in patch && !ACCESS_MODES.includes(patch.access_mode as never)) {
    return fail(400, `access_mode ${String(patch.access_mode)} is reserved or unknown.`)
  }
  if ('status' in patch && patch.status !== 'active' && patch.status !== 'disabled') {
    return fail(400, 'status may only be set to active or disabled.')
  }
  if ('idle_minutes' in patch) {
    const v = Number(patch.idle_minutes)
    if (!Number.isInteger(v) || v < IDLE_MINUTES_MIN || v > IDLE_MINUTES_MAX) {
      return fail(400, `idle_minutes must be between ${IDLE_MINUTES_MIN} and ${IDLE_MINUTES_MAX}.`)
    }
  }
  if ('max_session_hours' in patch) {
    const v = Number(patch.max_session_hours)
    if (!Number.isInteger(v) || v < MAX_SESSION_HOURS_MIN || v > MAX_SESSION_HOURS_MAX) {
      return fail(
        400,
        `max_session_hours must be between ${MAX_SESSION_HOURS_MIN} and ${MAX_SESSION_HOURS_MAX}.`,
      )
    }
  }
  if ('allowed_emails' in patch) {
    const list = patch.allowed_emails
    if (!Array.isArray(list) || list.some((e) => typeof e !== 'string' || !e.includes('@'))) {
      return fail(400, 'allowed_emails must be a list of addresses.')
    }
    patch.allowed_emails = (list as string[]).map((e) => e.trim().toLowerCase())
  }

  Object.assign(app, patch)
  app.live_state = deriveLiveState(app)
  if (app.status === 'disabled') {
    app.desired_count = 0
    app.running_count = 0
    app.awake_since = null
  }
  return json(app)
}

const encodeCursor = (offset: number) => btoa(JSON.stringify({ offset }))
function decodeCursor(cursor: string | null): number {
  if (!cursor) return 0
  try {
    return Number(JSON.parse(atob(cursor)).offset) || 0
  } catch {
    return 0
  }
}

// ---------------------------------------------------------------------------
// P2a — creation
// ---------------------------------------------------------------------------

function body(init: RequestInit): Record<string, unknown> {
  try {
    return JSON.parse(String(init.body ?? '{}')) as Record<string, unknown>
  } catch {
    return {}
  }
}

const uuid = () =>
  `${Date.now().toString(16)}-${Math.random().toString(16).slice(2, 10)}`

/** portal-p2a.md "Hostnames are policed", in the same order the API checks. */
const RESERVED_KEYS = [
  'www',
  'api',
  'auth',
  'admin',
  'proxy',
  'shinyplatform',
  'dashboards',
  'portal',
  'mail',
]

/**
 * A starter denylist. The real one lives in `__config__.key_denylist` so the
 * terms are editable without a deploy — and the refusal never says which term
 * matched, which is the point of it being a substring list.
 */
const KEY_DENYLIST = ['tarpeyo', 'nefecon', 'travere', 'calliditas']

/** The suffix length the real API reports. Mirrors creation.HOST_SUFFIX_CHARS. */
const SUFFIX_CHARS = 6

/** Lowercased base32 — the alphabet `creation.host_suffix` draws from. */
const SUFFIX_ALPHABET = 'abcdefghijklmnopqrstuvwxyz234567'

/**
 * A created app's hostname is `<key>-<random>`; the random half is minted on
 * the server at create time so an app cannot be found by guessing its name.
 * The mock mints one the same way (crypto, not Math.random) so mock mode
 * exercises the same shape the real API produces.
 */
function hostSuffix(): string {
  const drawn = crypto.getRandomValues(new Uint8Array(SUFFIX_CHARS))
  return Array.from(drawn, (n) => SUFFIX_ALPHABET[n % SUFFIX_ALPHABET.length]).join('')
}

function validateKey(raw: string): {
  ok: boolean
  host_preview?: string
  suffix_chars?: number
  reason?: string
} {
  const key = raw.trim().toLowerCase()

  if (key.length < KEY_MIN_LENGTH || key.length > KEY_MAX_LENGTH) {
    return {
      ok: false,
      reason: `A key is ${KEY_MIN_LENGTH}–${KEY_MAX_LENGTH} characters; that one is ${key.length}.`,
    }
  }
  if (!/^[a-z0-9-]+$/.test(key)) {
    return { ok: false, reason: 'Only lowercase letters, digits and hyphens.' }
  }
  if (key.startsWith('-') || key.endsWith('-') || key.includes('--')) {
    return { ok: false, reason: 'No leading, trailing or doubled hyphens.' }
  }
  if (RESERVED_KEYS.includes(key)) {
    return { ok: false, reason: `“${key}” is reserved by the platform.` }
  }
  if (KEY_DENYLIST.some((term) => key.includes(term))) {
    // Never echoes which term matched — see portal-p2a.md.
    return {
      ok: false,
      reason: 'That name can’t be used in a public hostname — pick a project codename.',
    }
  }
  if (state.some((a) => a.app_key === key)) {
    return { ok: false, reason: `An app already uses the key “${key}”.` }
  }
  // The SHAPE, not a hostname: this route reserves nothing, so any suffix it
  // returned would not be the one the app gets. See portal.py's _validate_key.
  return {
    ok: true,
    host_preview: `${key}-${'x'.repeat(SUFFIX_CHARS)}.tools.stratevi.com`,
    suffix_chars: SUFFIX_CHARS,
  }
}

let failNext = false

interface MockBuild {
  started_at: number
  polls: number
  outcome: 'succeeded' | 'failed'
}

const builds = new Map<string, MockBuild>()

/** Enough polls to see the phases move without waiting a real 15 minutes. */
const PHASES = [
  'SUBMITTED',
  'QUEUED',
  'PROVISIONING',
  'DOWNLOAD_SOURCE',
  'INSTALL',
  'PRE_BUILD',
  'BUILD',
  'POST_BUILD',
]

async function createApp(init: RequestInit): Promise<Response> {
  const payload = body(init)
  const key = String(payload.key ?? '')

  const verdict = validateKey(key)
  if (!verdict.ok) {
    // A key that collides is a 409; anything else is a 400.
    const conflict = verdict.reason?.startsWith('An app already uses')
    return fail(conflict ? 409 : 400, verdict.reason ?? 'Invalid key.')
  }
  if (!payload.upload_key) return fail(400, 'upload_key is required.')
  if (!('expires_at' in payload)) {
    return fail(400, 'expires_at must be present — send a date or null for never.')
  }
  if (!Array.isArray(payload.packages) || payload.packages.length === 0) {
    return fail(400, 'packages must be the confirmed list.')
  }
  const size = TASK_SIZES.find(
    (s) => s.cpu === Number(payload.cpu) && s.memory === Number(payload.memory),
  )
  if (!size) return fail(400, 'cpu/memory must be one of the two allowed sizes.')

  const host = `${key}-${hostSuffix()}.tools.stratevi.com`
  const app: App = {
    host,
    app_key: key,
    label: String(payload.label ?? key),
    description: String(payload.description ?? ''),
    ecs_service: `shiny-${key}`,
    container_port: 3838,
    status: 'building',
    live_state: 'building',
    access_mode: String(payload.access_mode ?? 'users'),
    allowed_emails: Array.isArray(payload.allowed_emails)
      ? (payload.allowed_emails as string[]).map((e) => e.trim().toLowerCase())
      : [],
    idle_minutes: Number(payload.idle_minutes ?? 20),
    max_session_hours: Number(payload.max_session_hours ?? 12),
    expires_at: payload.expires_at === null ? null : Number(payload.expires_at),
    last_active: null,
    awake_since: null,
    desired_count: 0,
    running_count: 0,
  }
  state.unshift(app)

  builds.set(host, {
    started_at: Math.floor(Date.now() / 1000),
    polls: 0,
    // A key containing "fail" (or __portalMock.failNextBuild()) takes the
    // failure path, so that screen is reachable in mock mode too.
    outcome: failNext || key.includes('fail') ? 'failed' : 'succeeded',
  })
  failNext = false

  return json(app, 202)
}

function buildStatus(host: string): Response {
  const build = builds.get(host)
  const app = state.find((a) => a.host === host)

  if (!build) {
    // The two seeded fixtures have no build record; synthesise one so the
    // build screen is reachable from the admin list on a cold reload.
    if (app?.live_state === 'building') {
      return json({
        state: 'building',
        phase: 'BUILD',
        started_at: Math.floor(Date.now() / 1000) - 7 * 60,
        elapsed_s: 7 * 60,
        log_url: consoleUrl(app.app_key),
        log_tail: fixtureBuildLog,
      })
    }
    if (app?.live_state === 'build_failed') {
      return json({
        state: 'failed',
        phase: 'BUILD',
        started_at: Math.floor(Date.now() / 1000) - 18 * 60,
        elapsed_s: 18 * 60,
        log_url: consoleUrl(app.app_key),
        log_tail: [...fixtureBuildLog, ...FAILURE_LINES],
      })
    }
    return fail(404, `No build is recorded for ${host}.`)
  }

  build.polls += 1
  const elapsed = Math.floor(Date.now() / 1000) - build.started_at
  const done = build.polls > PHASES.length
  const key = app?.app_key ?? host.split('.')[0]

  if (!done) {
    return json({
      state: 'building',
      phase: PHASES[Math.min(build.polls - 1, PHASES.length - 1)],
      started_at: build.started_at,
      elapsed_s: elapsed,
      log_url: consoleUrl(key),
      log_tail: fixtureBuildLog.slice(0, 2 + build.polls * 2),
    })
  }

  if (build.outcome === 'failed') {
    if (app) {
      app.status = 'build_failed'
      app.live_state = 'build_failed'
    }
    return json({
      state: 'failed',
      phase: 'BUILD',
      started_at: build.started_at,
      elapsed_s: elapsed,
      log_url: consoleUrl(key),
      log_tail: [...fixtureBuildLog, ...FAILURE_LINES],
    })
  }

  if (app) {
    app.status = 'active'
    app.live_state = 'asleep'
  }
  return json({
    state: 'succeeded',
    phase: 'COMPLETED',
    started_at: build.started_at,
    elapsed_s: elapsed,
    log_url: consoleUrl(key),
    log_tail: [
      ...fixtureBuildLog,
      '* DONE (networkD3)',
      `Successfully tagged shiny-${key}:r1`,
      '[Container] Phase complete: BUILD State: SUCCEEDED',
    ],
  })
}

const FAILURE_LINES = [
  "ERROR: dependency ‘StanHeaders’ is not available for package ‘rstan’",
  '* removing ‘/usr/local/lib/R/site-library/rstan’',
  'Error: installation of package ‘rstan’ had non-zero exit status',
  '[Container] Phase complete: BUILD State: FAILED',
]

const consoleUrl = (key: string) =>
  `https://us-east-1.console.aws.amazon.com/codesuite/codebuild/652063276768/projects/shiny-app-build/history?region=us-east-1&search=${encodeURIComponent(key)}`

/**
 * Stands in for the browser's PUT to S3 in mock mode. It fires the same
 * progress callbacks XHR would, so the upload UI is exercised for real
 * without a bucket.
 */
export async function mockUpload(file: File, options: UploadOptions): Promise<void> {
  const steps = 12
  for (let i = 1; i <= steps; i++) {
    if (options.signal?.aborted) {
      throw new DOMException('Upload aborted', 'AbortError')
    }
    // Bigger files take visibly longer, which is the point of the bar.
    await new Promise((r) => setTimeout(r, 40 + Math.min(file.size / 2_000_000, 60)))
    options.onProgress?.(i / steps)
  }
}
