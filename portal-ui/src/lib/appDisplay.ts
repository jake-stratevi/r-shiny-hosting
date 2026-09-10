// Turning a row into the short human phrases the cards and rows are made of.
// Kept out of the components so the wording is testable and said the same way
// in every place it appears.

import type { App, LiveState } from '../api/types'
import { relativeTime } from './time'

/** "8 people" / "all staff" — assembled.work's access summary, our vocabulary. */
export function accessSummary(app: Pick<App, 'access_mode' | 'allowed_emails'>): string {
  if (app.access_mode === 'all_users') return 'Everyone signed in'
  if (app.access_mode === 'users') {
    const n = app.allowed_emails?.length ?? 0
    if (n === 0) return 'Nobody yet'
    return `${n} ${n === 1 ? 'person' : 'people'}`
  }
  // A reserved mode the proxy fails closed on. Say so, don't dress it up.
  return `${String(app.access_mode).replace(/_/g, ' ')} — reserved`
}

/** True when the row is in a state that refuses every request. */
export function accessIsBroken(app: Pick<App, 'access_mode' | 'allowed_emails'>): boolean {
  if (app.access_mode === 'all_users') return false
  if (app.access_mode === 'users') return (app.allowed_emails?.length ?? 0) === 0
  return true
}

export interface ExpirySummary {
  text: string
  /** `soon` = inside a week, `past` = already gone. */
  tone: 'never' | 'ok' | 'soon' | 'past'
}

const WEEK_SECONDS = 7 * 24 * 60 * 60

/** "Never" / "in 30 days" / "4 days ago". */
export function expirySummary(
  expiresAt: number | null | undefined,
  now = Date.now(),
): ExpirySummary {
  if (expiresAt === null || expiresAt === undefined) return { text: 'Never', tone: 'never' }
  const delta = expiresAt - now / 1000
  const text = relativeTime(expiresAt, now)
  if (delta <= 0) return { text, tone: 'past' }
  return { text, tone: delta < WEEK_SECONDS ? 'soon' : 'ok' }
}

/** The same fact as a sentence fragment: "Expired 4 days ago", not "Expires 4 days ago". */
export function expiryPhrase(summary: ExpirySummary): string {
  if (summary.tone === 'never') return 'Never expires'
  if (summary.tone === 'past') return `Expired ${summary.text}`
  return `Expires ${summary.text}`
}

/**
 * The one thing every user of a scale-to-zero app needs told before they
 * click. Cold start is a container pull plus R's startup, so the honest
 * number is tens of seconds, not "instant".
 */
export const WAKE_HINT = 'Starts in ~30–60s when opened'

/** The line under a tile's title: what clicking it will feel like. */
export function liveStateHint(state: LiveState | string): string | null {
  switch (state) {
    case 'awake':
      return 'Ready now'
    case 'starting':
      return 'Starting up — give it a moment'
    case 'asleep':
      return WAKE_HINT
    case 'disabled':
      return 'Turned off by an administrator'
    case 'expired':
      return 'Access to this has expired'
    case 'building':
      return BUILD_HINT
    case 'build_failed':
      return 'Its first build failed — it has never run'
    default:
      return null
  }
}

/**
 * The honest version of "almost there". R packages compile from source, so a
 * first build is minutes, not seconds — portal-p2a.md says to say so.
 */
export const BUILD_HINT = 'Still building — first builds take 10–20 minutes'

/** Rows an admin should look at today, in the order they should look. */
export function needsAttention(app: App): string | null {
  if (app.status === 'build_failed' || app.live_state === 'build_failed') {
    return 'Build failed'
  }
  // A build in flight is progress, not a problem; it gets its own chip.
  if (app.status === 'building' || app.live_state === 'building') return null
  if (app.status === 'expired' || app.live_state === 'expired') return 'Expired'
  if (accessIsBroken(app)) {
    return app.access_mode === 'users' ? 'No one can open it' : 'Reserved access mode'
  }
  const expiry = expirySummary(app.expires_at)
  if (expiry.tone === 'soon') return `Expires ${expiry.text}`
  if (app.status === 'disabled') return 'Disabled'
  return null
}

/**
 * The same flag, but only when it adds something to the row it sits in. A
 * list row already shows a status chip and an expiry column, so an
 * "Expired" pill next to an Expired chip — or "Expires in 6 days" next to an
 * amber "in 6 days" — is noise. What a row cannot show any other way is a
 * broken access mode, so that is what survives.
 */
export function attentionFlag(app: App): string | null {
  const flag = needsAttention(app)
  if (!flag) return null
  // Underscores folded so "Build failed" is recognised as `build_failed`.
  if (flag.toLowerCase().replace(/\s+/g, '_') === String(app.live_state).toLowerCase()) {
    return null
  }
  if (flag.startsWith('Expires ')) return null
  return flag
}

/** "jake@stratevi.com" -> "Jake". Falls back to something addressable. */
export function firstNameFromEmail(email: string | null | undefined): string {
  const local = (email ?? '').split('@')[0]?.trim()
  if (!local) return 'there'
  const part = local.split(/[._-]/).filter(Boolean)[0]
  if (!part) return 'there'
  const cleaned = part.replace(/\d+$/, '')
  const word = cleaned || part
  return word.charAt(0).toUpperCase() + word.slice(1)
}

export function greeting(now = new Date()): string {
  const hour = now.getHours()
  if (hour < 12) return 'Good morning'
  return hour < 18 ? 'Good afternoon' : 'Good evening'
}

/**
 * assembled.work's "pulse": one sentence about the account, urgency first,
 * calm otherwise. Ours counts awake tools rather than published sites.
 */
export function menuPulse(states: Array<LiveState | string>): string {
  const total = states.length
  if (total === 0) return 'Nothing is shared with you yet.'
  const awake = states.filter((s) => s === 'awake').length
  const starting = states.filter((s) => s === 'starting').length
  const tool = total === 1 ? 'tool' : 'tools'

  if (starting > 0) {
    return `${starting} starting up · ${awake} of your ${total} ${tool} ready.`
  }
  if (awake === 0) {
    return `All ${total === 1 ? 'quiet' : `${total} ${tool} quiet`} — they start when you open them.`
  }
  return `${awake} of your ${total} ${tool} ${awake === 1 ? 'is' : 'are'} awake right now.`
}
