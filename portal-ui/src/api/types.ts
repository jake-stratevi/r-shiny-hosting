// Hand-written from docs/design/portal-api.md (v1, P1 scope).
// If that file changes, change this one in the same commit.

/** Stored status. The reaper owns `expired`; the UI may only set the other two. */
export type AppStatus = 'active' | 'disabled' | 'expired'

/** Statuses a PATCH is allowed to write (see PATCH /api/v1/apps/{host}). */
export type WritableAppStatus = Extract<AppStatus, 'active' | 'disabled'>

/** Derived from ECS + the row. */
export type LiveState = 'awake' | 'starting' | 'asleep' | 'disabled' | 'expired'

/** Implemented by the proxy. */
export type AccessMode = 'all_users' | 'users'

/**
 * Enumerated in proxy_app/registry.py; the API refuses them with 400. Shown
 * in the UI greyed out so the roadmap is visible without being clickable.
 */
export type ReservedAccessMode = 'team' | 'organizations' | 'client_magic_link'

export const ACCESS_MODES: readonly AccessMode[] = ['all_users', 'users']
export const RESERVED_ACCESS_MODES: readonly ReservedAccessMode[] = [
  'team',
  'organizations',
  'client_magic_link',
]

/** GET /api/v1/me */
export interface Me {
  email: string
  is_admin: boolean
}

/** One tile from GET /api/v1/menu */
export interface MenuApp {
  host: string
  label: string
  description: string
  url: string
  live_state: LiveState
}

export interface MenuResponse {
  apps: MenuApp[]
}

/** GET /api/v1/apps, GET/PATCH /api/v1/apps/{host} */
export interface App {
  host: string
  app_key: string
  label: string
  description: string
  ecs_service: string
  container_port: number
  status: AppStatus
  live_state: LiveState
  access_mode: AccessMode | ReservedAccessMode | string
  allowed_emails: string[]
  idle_minutes: number
  max_session_hours: number
  /** epoch seconds, null = never expires */
  expires_at: number | null
  /** epoch seconds, null if never seen */
  last_active: number | null
  /** epoch seconds, null while asleep */
  awake_since: number | null
  desired_count: number
  running_count: number
}

/** Body of PATCH /api/v1/apps/{host}. Any subset; unknown fields are a 400. */
export interface AppPatch {
  label?: string
  description?: string
  access_mode?: AccessMode
  allowed_emails?: string[]
  /** 1-1440 */
  idle_minutes?: number
  /** 0-168, 0 = uncapped */
  max_session_hours?: number
  /** epoch seconds, or null for never */
  expires_at?: number | null
  status?: WritableAppStatus
}

/** GET /api/v1/apps/{host}/audit */
export interface AuditEvent {
  /** Open-ended: `allow`, `deny`, `wake`, `config_change`, ... */
  event: string
  email: string
  path: string
  /** epoch seconds */
  ts: number
}

export interface AuditPage {
  events: AuditEvent[]
  /** Opaque base64-JSON DynamoDB LastEvaluatedKey; null = end of the log. */
  cursor: string | null
}

/** Non-2xx bodies. */
export interface ApiErrorBody {
  error: string
}

export const IDLE_MINUTES_MIN = 1
export const IDLE_MINUTES_MAX = 1440
export const MAX_SESSION_HOURS_MIN = 0
export const MAX_SESSION_HOURS_MAX = 168
