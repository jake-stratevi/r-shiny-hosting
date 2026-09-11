// Hand-written from docs/design/portal-api.md (v1, P1 scope).
// If that file changes, change this one in the same commit.

/**
 * Stored status. The reaper owns `expired`, the creation pipeline owns
 * `building` / `build_failed` (P2a); the UI may only set the first two.
 */
export type AppStatus =
  | 'active'
  | 'disabled'
  | 'expired'
  | 'building'
  | 'build_failed'

/** Statuses a PATCH is allowed to write (see PATCH /api/v1/apps/{host}). */
export type WritableAppStatus = Extract<AppStatus, 'active' | 'disabled'>

/** Derived from ECS + the row. `building` / `build_failed` are P2a additions. */
export type LiveState =
  | 'awake'
  | 'starting'
  | 'asleep'
  | 'disabled'
  | 'expired'
  | 'building'
  | 'build_failed'

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
  /**
   * P2a. From `__config__.creator_emails`, and deliberately INDEPENDENT of
   * `is_admin` (portal-p2a.md "Decisions"): an admin without it may not
   * create. Optional here so a P1 backend that omits the field reads as
   * `undefined` -> falsy -> no "+" affordance, which is the fail-closed
   * behaviour the decision asks for.
   */
  can_create?: boolean
}

/** One tile from GET /api/v1/menu */
export interface MenuApp {
  host: string
  label: string
  description: string
  url: string
  live_state: LiveState
  /** Null = never expires. On the menu because the tile shows it. */
  expires_at: number | null
  /** Null = nobody has ever opened it. */
  last_active: number | null
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

// ---------------------------------------------------------------------------
// P2a — creation. See portal-api.md "P2a additions" and portal-p2a.md.
// ---------------------------------------------------------------------------

/** POST /api/v1/apps/validate-key — 200 either way; this is a form affordance. */
export interface ValidateKeyResult {
  ok: boolean
  /**
   * Present when `ok`: the SHAPE of the hostname, with the random suffix
   * standing in as `xxxxxx` — e.g. `q3-uptake-xxxxxx.tools.stratevi.com`.
   *
   * Not the hostname, and deliberately. A created app's address carries a
   * random suffix minted server-side at CREATE time so it cannot be guessed
   * by anyone not on the platform; this route reserves nothing, so any
   * suffix it returned would be a different one from the one the app gets.
   * The wizard therefore shows the shape and says the suffix is added on
   * create; the real address appears on the build screen.
   */
  host_preview?: string
  /** How many characters the real suffix will have. */
  suffix_chars?: number
  /** Present when not `ok`: shown to the user verbatim. */
  reason?: string
}

/** POST /api/v1/uploads */
export interface UploadTicket {
  upload_key: string
  /** Presigned S3 PUT. The browser sends the bytes; the API never proxies them. */
  url: string
  expires_in: number
}

// There is no inspect route. The detected entrypoint and package list are
// read from the zip in the browser, before the upload — see
// `src/lib/inspectBundle.ts` and portal-api.md, "Bundle inspection is
// CLIENT-SIDE". `packages` below is the list the user confirmed.

/** POST /api/v1/apps — the wizard's four steps plus the upload key. */
export interface CreateAppBody {
  key: string
  label: string
  description: string
  /** Fargate CPU units / MiB. One of the two sizes in TASK_SIZES. */
  cpu: number
  memory: number
  upload_key: string
  access_mode: AccessMode
  allowed_emails: string[]
  idle_minutes: number
  max_session_hours: number
  /** Must be explicitly present — epoch seconds, or null for never. */
  expires_at: number | null
  /** The confirmed list, recorded on the release so a rebuild reproduces it. */
  packages: string[]
}

export type BuildState = 'building' | 'succeeded' | 'failed'

/** GET /api/v1/apps/{host}/build */
export interface BuildStatus {
  state: BuildState
  /** A CodeBuild phase name; render unknown values as-is. */
  phase: string
  started_at: number
  elapsed_s: number
  /** CloudWatch/CodeBuild console deep link. */
  log_url: string
  log_tail: string[]
}

export type TaskSizeId = 'dashboard' | 'model'

export interface TaskSize {
  id: TaskSizeId
  /** Fargate units. */
  cpu: number
  memory: number
  label: string
  /** What this size is for, in the words the platform already uses. */
  blurb: string
  /** Per awake hour, from CLAUDE.md's cost model. */
  hourly: string
  /** The idle timeout this size usually wants (portal-p2a.md UI section). */
  suggestedIdleMinutes: number
}

/**
 * The only two sizes the API accepts. Both come from CLAUDE.md's cost model;
 * ADR-0001's rule ("Fargate bills per second — size up rather than down") is
 * shown next to them in the wizard rather than buried in a doc.
 */
export const TASK_SIZES: readonly TaskSize[] = [
  {
    id: 'dashboard',
    cpu: 512,
    memory: 2048,
    label: '0.5 vCPU / 2 GB',
    blurb: 'Dashboards — Sankeys, cross-tabs, anything that just renders.',
    hourly: '$0.029 per awake hour',
    suggestedIdleMinutes: 20,
  },
  {
    id: 'model',
    cpu: 4096,
    memory: 16384,
    label: '4 vCPU / 16 GB',
    blurb: 'Models — microsimulations and anything CPU-bound.',
    hourly: '$0.233 per awake hour',
    suggestedIdleMinutes: 10,
  },
]

export const taskSize = (id: TaskSizeId): TaskSize =>
  TASK_SIZES.find((s) => s.id === id) ?? TASK_SIZES[0]

/** portal-p2a.md "Validation": the zip cap, checked here before we ask for a URL. */
export const MAX_ZIP_BYTES = 100 * 1024 * 1024
export const MAX_ZIP_MB = 100

/** Key shape, from portal-p2a.md "Hostnames are policed". The API re-checks. */
export const KEY_MIN_LENGTH = 3
export const KEY_MAX_LENGTH = 30

// ---------------------------------------------------------------------------
// Costs. See portal-api.md "Costs — per-app awake time and estimated spend".
//
// Every figure here is an ESTIMATE derived from recorded awake time and
// published Fargate rates, never a billed amount. The payload carries its own
// `disclaimer` string and the UI must show it.
// ---------------------------------------------------------------------------

/** One day of a single app's awake time. Quiet days are omitted. */
export interface CostDay {
  day: string
  awake_hours: number
}

/** One app's line for one month. */
export interface AppCost {
  host: string
  label: string
  app_key: string
  /** Fargate units. 0/0 when the row predates the portal and is unrecognised. */
  cpu: number
  memory: number
  /**
   * False when the app's task size is unknown — a hand-provisioned row whose
   * `app_key` is not one of the two known sizes. `estimated_cost` is then 0
   * and `hourly_rate` null, because guessing a size would put a fabricated
   * number on a screen labelled "cost".
   */
  size_known: boolean
  hourly_rate: number | null
  awake_hours: number
  estimated_cost: number
  /** epoch seconds; null = never ran in this period. */
  last_run: number | null
  currently_awake: boolean
  daily: CostDay[]
  /**
   * Open map of degenerate-event counts from the audit trail
   * (`unclosed`, `duplicate_wake`, `orphan_close`, ...). Render unknown keys
   * neutrally, or not at all.
   */
  anomalies: Record<string, number>
}

/** One itemised shared charge. Never divided across apps. */
export interface OverheadLine {
  name: string
  monthly: number
  note: string
}

export interface Overhead {
  shared: true
  lines: OverheadLine[]
  monthly_total: number
  /** Pro-rated by elapsed time — a fixed charge, not a per-app allocation. */
  to_date_total: number
  elapsed_fraction: number
  note: string
}

/** One period block: per-app compute, the shared line, and a total. */
export interface CostPeriod {
  /** `YYYY-MM` */
  month: string
  start: number
  end: number
  complete: boolean
  apps: AppCost[]
  apps_total: number
  overhead: Overhead
  total: number
}

export interface Rates {
  vcpu_hour: number
  gb_hour: number
  region: string
  source: string
}

/** GET /api/v1/costs */
export interface CostsReport {
  currency: string
  basis: string
  generated_at: number
  /** The ledger could not be read; per-app figures may be incomplete. */
  stale: boolean
  rates: Rates
  disclaimer: string
  month_to_date: CostPeriod
  previous_month: CostPeriod
}

/** GET /api/v1/apps/{host}/costs */
export interface AppCostsReport {
  currency: string
  basis: string
  generated_at: number
  rates: Rates
  disclaimer: string
  month_to_date: AppCost
  previous_month: AppCost
}
