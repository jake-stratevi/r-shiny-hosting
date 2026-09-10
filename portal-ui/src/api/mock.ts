// Mock transport for `npm run dev:mock`. Loaded lazily by client.ts and only
// when VITE_PORTAL_MOCK=1, so it costs the production bundle nothing.
//
// It returns real `Response` objects, which means the client's CSRF, JSON and
// error handling run exactly as they do against the live service.

import { fixtureApps, fixtureAudit, fixtureMe } from './fixtures'
import {
  ACCESS_MODES,
  IDLE_MINUTES_MAX,
  IDLE_MINUTES_MIN,
  MAX_SESSION_HOURS_MAX,
  MAX_SESSION_HOURS_MIN,
  type App,
  type AppPatch,
  type LiveState,
} from './types'

const LATENCY_MS = 180

// Mutable copy so PATCHes stick for the life of the page.
const state: App[] = fixtureApps.map((a) => ({ ...a, allowed_emails: [...a.allowed_emails] }))
const ADMIN_KEY = 'portalMockAdmin'

function storedAdmin(): boolean | null {
  try {
    const raw = localStorage.getItem(ADMIN_KEY)
    if (raw === '0') return false
    if (raw === '1') return true
  } catch {
    /* private mode, whatever */
  }
  return null
}

let me = { ...fixtureMe, is_admin: storedAdmin() ?? fixtureMe.is_admin }

/**
 * Flip the admin gate from the console and reload to see the non-admin view:
 *   __portalMock.setAdmin(false)
 */
declare global {
  // eslint-disable-next-line no-var
  var __portalMock: { setAdmin(v: boolean): void; apps(): App[] } | undefined
}
globalThis.__portalMock = {
  setAdmin(v: boolean) {
    me = { ...me, is_admin: v }
    try {
      localStorage.setItem(ADMIN_KEY, v ? '1' : '0')
    } catch {
      /* ignore */
    }
  },
  apps: () => state,
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

  if (pathname.startsWith('/api/v1/apps')) {
    if (!me.is_admin) return fail(403, 'Admin access is required.')
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
