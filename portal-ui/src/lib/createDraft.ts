// The creation wizard's state and its rules, kept out of the component so
// "why is Continue disabled?" is one testable function rather than a tangle
// of `disabled={...}` expressions.
//
// Contract: docs/design/portal-api.md "P2a additions — creation".
// Product:  docs/design/portal-p2a.md, UI + Decisions.

import {
  IDLE_MINUTES_MAX,
  IDLE_MINUTES_MIN,
  MAX_SESSION_HOURS_MAX,
  MAX_SESSION_HOURS_MIN,
  MAX_ZIP_BYTES,
  MAX_ZIP_MB,
  taskSize,
  type AccessMode,
  type CreateAppBody,
  type TaskSizeId,
} from '../api/types'
import type { BundleInspection } from './inspectBundle'

export type StepId = 'details' | 'upload' | 'access' | 'expiry' | 'review'

export const WIZARD_STEPS: ReadonlyArray<{ id: StepId; label: string }> = [
  { id: 'details', label: 'Details' },
  { id: 'upload', label: 'Upload' },
  { id: 'access', label: 'Access' },
  { id: 'expiry', label: 'Expiry' },
  { id: 'review', label: 'Review' },
]

export const stepIndex = (id: StepId): number =>
  WIZARD_STEPS.findIndex((s) => s.id === id)

/** How the key check reads to the rest of the wizard. */
export type KeyCheckState = 'idle' | 'checking' | 'ok' | 'rejected' | 'unknown'

export interface KeyCheck {
  state: KeyCheckState
  /** The hostname the API says the key resolves to, once it says `ok`. */
  host: string | null
  /** The API's own rejection text, shown verbatim. */
  reason: string | null
}

export const IDLE_KEY_CHECK: KeyCheck = { state: 'idle', host: null, reason: null }

/**
 * Where the zip is in its journey to S3. `inspecting` comes FIRST: the
 * bundle is read in this browser before a single byte is uploaded, so a
 * bundle with no entrypoint fails in a second rather than after a 100 MB
 * PUT. See lib/inspectBundle.ts.
 */
export type UploadPhase =
  | 'empty'
  | 'inspecting'
  | 'requesting'
  | 'uploading'
  | 'ready'
  | 'failed'

export interface CreateDraft {
  label: string
  key: string
  description: string
  size: TaskSizeId

  file: File | null
  uploadPhase: UploadPhase
  /** 0..1, from XHR's real progress events. */
  uploadProgress: number
  uploadKey: string | null
  /** What reading the zip found. Null when it could not be read. */
  inspection: BundleInspection | null
  /** Why inspection produced nothing, in words for the user. */
  inspectionError: string | null
  /** Explicit confirmation. portal-p2a.md: "Never silently guess". */
  packagesConfirmed: boolean
  /** One package per line; seeded from the inspection, editable. */
  packagesText: string
  uploadError: string | null

  access_mode: AccessMode
  allowed_emails: string[]
  /** Strings, so a half-typed number does not become NaN mid-keystroke. */
  idle_minutes: string
  max_session_hours: string

  /**
   * Separate from `expires_at`, because null means "never" and the API
   * rejects an absent value — so "not decided yet" needs its own bit.
   */
  expiryChosen: boolean
  expires_at: number | null
}

/**
 * `idle_minutes` starts at 20 (portal-p2a.md's dashboard default); the model
 * size shows a note suggesting 10 rather than silently rewriting the field
 * under the user's cursor.
 */
export const DEFAULT_IDLE_MINUTES = 20
export const DEFAULT_MAX_SESSION_HOURS = 12

export function emptyDraft(): CreateDraft {
  return {
    label: '',
    key: '',
    description: '',
    size: 'dashboard',
    file: null,
    uploadPhase: 'empty',
    uploadProgress: 0,
    uploadKey: null,
    inspection: null,
    inspectionError: null,
    packagesConfirmed: false,
    packagesText: '',
    uploadError: null,
    access_mode: 'users',
    allowed_emails: [],
    idle_minutes: String(DEFAULT_IDLE_MINUTES),
    max_session_hours: String(DEFAULT_MAX_SESSION_HOURS),
    expiryChosen: false,
    expires_at: null,
  }
}

/** "shiny app.zip" -> "shiny-app": a starting point, never a silent commit. */
export function suggestKey(label: string): string {
  return label
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 30)
    .replace(/-+$/, '')
}

/** The confirmed list, in the order it will be recorded on the release. */
export function packagesFrom(draft: CreateDraft): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const line of draft.packagesText.split(/[\s,]+/)) {
    const name = line.trim()
    if (name === '' || seen.has(name)) continue
    seen.add(name)
    out.push(name)
  }
  return out
}

export interface FileProblem {
  message: string
}

/** Client-side pre-check. The API re-validates; this just saves a round trip. */
export function checkZip(file: File): FileProblem | null {
  if (!file.name.toLowerCase().endsWith('.zip')) {
    return { message: 'That is not a .zip. Zip the app directory and try again.' }
  }
  if (file.size === 0) {
    return { message: 'That file is empty.' }
  }
  if (file.size > MAX_ZIP_BYTES) {
    return {
      message: `That zip is ${humanSize(file.size)}, over the ${MAX_ZIP_MB} MB limit. Move large data files to S3 and read them at runtime.`,
    }
  }
  return null
}

export function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

/**
 * Why Next is disabled, or null when it isn't. Every branch returns a
 * sentence the user can act on — a disabled button with no reason next to it
 * is the single worst thing a wizard can do.
 */
export function stepBlocker(
  step: StepId,
  draft: CreateDraft,
  keyCheck: KeyCheck,
): string | null {
  switch (step) {
    case 'details':
      return detailsBlocker(draft, keyCheck)
    case 'upload':
      return uploadBlocker(draft)
    case 'access':
      return accessBlocker(draft)
    case 'expiry':
      return draft.expiryChosen
        ? null
        : 'Choose an expiry date or “never expires”. There is no default — the API rejects a create without one.'
    case 'review':
      return (
        detailsBlocker(draft, keyCheck) ?? uploadBlocker(draft) ?? accessBlocker(draft) ??
        (draft.expiryChosen ? null : 'Go back and make an expiry choice.')
      )
  }
}

function detailsBlocker(draft: CreateDraft, keyCheck: KeyCheck): string | null {
  if (draft.label.trim() === '') return 'Give the app a label — it is what the tile says.'
  if (draft.key.trim() === '') return 'Choose a key. It becomes the hostname.'
  switch (keyCheck.state) {
    case 'ok':
      return null
    case 'checking':
      return 'Checking that key…'
    case 'rejected':
      return keyCheck.reason ?? 'That key cannot be used.'
    case 'unknown':
      return 'That key could not be checked just now. Try again before continuing.'
    case 'idle':
      return 'Waiting for the key check.'
  }
}

function uploadBlocker(draft: CreateDraft): string | null {
  if (draft.file === null) return 'Choose the app’s .zip.'
  if (draft.uploadError) return draft.uploadError
  if (draft.uploadPhase !== 'ready' || draft.uploadKey === null) {
    return 'Wait for the upload to finish.'
  }
  if (packagesFrom(draft).length === 0) {
    return 'List the R packages the app needs — the build installs exactly this list.'
  }
  if (!draft.packagesConfirmed) {
    return 'Confirm the entrypoint and package list before continuing.'
  }
  return null
}

function accessBlocker(draft: CreateDraft): string | null {
  if (draft.access_mode === 'users' && draft.allowed_emails.length === 0) {
    return 'Add at least one address, or switch to everyone signed in — an empty list locks everybody out.'
  }
  const idle = Number(draft.idle_minutes)
  if (
    draft.idle_minutes.trim() === '' ||
    !Number.isInteger(idle) ||
    idle < IDLE_MINUTES_MIN ||
    idle > IDLE_MINUTES_MAX
  ) {
    return `Idle timeout must be a whole number of minutes between ${IDLE_MINUTES_MIN} and ${IDLE_MINUTES_MAX}.`
  }
  const cap = Number(draft.max_session_hours)
  if (
    draft.max_session_hours.trim() === '' ||
    !Number.isInteger(cap) ||
    cap < MAX_SESSION_HOURS_MIN ||
    cap > MAX_SESSION_HOURS_MAX
  ) {
    return `Session cap must be a whole number of hours between ${MAX_SESSION_HOURS_MIN} and ${MAX_SESSION_HOURS_MAX} (0 = uncapped).`
  }
  return null
}

/** The POST body. Only ever called once every blocker is clear. */
export function toCreateBody(draft: CreateDraft): CreateAppBody {
  const size = taskSize(draft.size)
  return {
    key: draft.key.trim(),
    label: draft.label.trim(),
    description: draft.description.trim(),
    cpu: size.cpu,
    memory: size.memory,
    upload_key: draft.uploadKey ?? '',
    access_mode: draft.access_mode,
    allowed_emails: draft.access_mode === 'users' ? draft.allowed_emails : [],
    idle_minutes: Number(draft.idle_minutes),
    max_session_hours: Number(draft.max_session_hours),
    // Explicitly present, always — never omitted, and null means never.
    expires_at: draft.expires_at,
    packages: packagesFrom(draft),
  }
}
