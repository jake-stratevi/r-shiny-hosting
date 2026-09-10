import { describe, expect, it } from 'vitest'
import type { App } from '../api/types'
import {
  accessIsBroken,
  accessSummary,
  attentionFlag,
  expiryPhrase,
  expirySummary,
  firstNameFromEmail,
  greeting,
  liveStateHint,
  menuPulse,
  needsAttention,
  WAKE_HINT,
} from './appDisplay'

const NOW = 1_800_000_000_000 // fixed clock, ms
const DAY = 86_400

function app(overrides: Partial<App> = {}): App {
  return {
    host: 'a.tools.stratevi.com',
    app_key: 'a',
    label: 'A',
    description: '',
    ecs_service: 'shiny-a',
    container_port: 3838,
    status: 'active',
    live_state: 'asleep',
    access_mode: 'users',
    allowed_emails: ['jake@stratevi.com'],
    idle_minutes: 15,
    max_session_hours: 12,
    expires_at: null,
    last_active: null,
    awake_since: null,
    desired_count: 0,
    running_count: 0,
    ...overrides,
  }
}

describe('accessSummary', () => {
  it('counts people, singular and plural', () => {
    expect(accessSummary({ access_mode: 'users', allowed_emails: ['a@b.com'] })).toBe('1 person')
    expect(
      accessSummary({ access_mode: 'users', allowed_emails: ['a@b.com', 'c@d.com'] }),
    ).toBe('2 people')
  })

  it('says so when the list is empty', () => {
    expect(accessSummary({ access_mode: 'users', allowed_emails: [] })).toBe('Nobody yet')
  })

  it('names the open mode', () => {
    expect(accessSummary({ access_mode: 'all_users', allowed_emails: [] })).toBe(
      'Everyone signed in',
    )
  })

  it('flags a reserved mode rather than pretending it works', () => {
    expect(accessSummary({ access_mode: 'client_magic_link', allowed_emails: [] })).toBe(
      'client magic link — reserved',
    )
  })

  it('knows which of those states refuse everyone', () => {
    expect(accessIsBroken({ access_mode: 'all_users', allowed_emails: [] })).toBe(false)
    expect(accessIsBroken({ access_mode: 'users', allowed_emails: ['a@b.com'] })).toBe(false)
    expect(accessIsBroken({ access_mode: 'users', allowed_emails: [] })).toBe(true)
    expect(accessIsBroken({ access_mode: 'team', allowed_emails: [] })).toBe(true)
  })
})

describe('expirySummary', () => {
  it('calls null never', () => {
    expect(expirySummary(null, NOW)).toEqual({ text: 'Never', tone: 'never' })
  })

  it('marks anything inside a week as soon', () => {
    expect(expirySummary(NOW / 1000 + 3 * DAY, NOW).tone).toBe('soon')
    expect(expirySummary(NOW / 1000 + 30 * DAY, NOW).tone).toBe('ok')
  })

  it('marks a past date as past, with an "ago" phrasing', () => {
    const past = expirySummary(NOW / 1000 - 4 * DAY, NOW)
    expect(past.tone).toBe('past')
    expect(past.text).toBe('4 days ago')
  })

  it('reads as a sentence fragment in the right tense', () => {
    expect(expiryPhrase(expirySummary(null, NOW))).toBe('Never expires')
    expect(expiryPhrase(expirySummary(NOW / 1000 - 4 * DAY, NOW))).toBe('Expired 4 days ago')
    expect(expiryPhrase(expirySummary(NOW / 1000 + 30 * DAY, NOW))).toBe('Expires in 1 month')
  })
})

describe('needsAttention', () => {
  it('is quiet about a healthy app', () => {
    expect(needsAttention(app({ expires_at: NOW / 1000 + 90 * DAY }))).toBeNull()
  })

  it('leads with expiry over everything else', () => {
    expect(needsAttention(app({ status: 'expired', live_state: 'expired' }))).toBe('Expired')
  })

  it('catches an allowlist nobody is on', () => {
    expect(needsAttention(app({ allowed_emails: [] }))).toBe('No one can open it')
  })

  it('catches a reserved access mode', () => {
    expect(needsAttention(app({ access_mode: 'team' }))).toBe('Reserved access mode')
  })

  it('catches a disabled app', () => {
    expect(needsAttention(app({ status: 'disabled', live_state: 'disabled' }))).toBe('Disabled')
  })

  it('does not repeat what the rest of the row already says', () => {
    expect(attentionFlag(app({ status: 'disabled', live_state: 'disabled' }))).toBeNull()
    expect(attentionFlag(app({ status: 'expired', live_state: 'expired' }))).toBeNull()
    // The expiry column already carries this, in amber.
    expect(attentionFlag(app({ expires_at: Date.now() / 1000 + 2 * DAY }))).toBeNull()
    // Nothing else on the row can say this.
    expect(attentionFlag(app({ allowed_emails: [] }))).toBe('No one can open it')
    expect(attentionFlag(app({ access_mode: 'team' }))).toBe('Reserved access mode')
  })
})

describe('menu copy', () => {
  it('tells a user what opening a sleeping tool will feel like', () => {
    expect(liveStateHint('asleep')).toBe(WAKE_HINT)
    expect(liveStateHint('awake')).toBe('Ready now')
    expect(liveStateHint('nonsense-from-p2')).toBeNull()
  })

  it('summarises the account in one sentence', () => {
    expect(menuPulse([])).toMatch(/nothing is shared/i)
    expect(menuPulse(['awake', 'asleep', 'asleep'])).toBe('1 of your 3 tools is awake right now.')
    expect(menuPulse(['asleep', 'asleep'])).toMatch(/quiet/i)
    expect(menuPulse(['starting', 'awake'])).toMatch(/^1 starting up/)
  })

  it('finds a first name in an email, or gives up gracefully', () => {
    expect(firstNameFromEmail('jake@stratevi.com')).toBe('Jake')
    expect(firstNameFromEmail('mary.lou@stratevi.com')).toBe('Mary')
    expect(firstNameFromEmail('nick2@stratevi.com')).toBe('Nick')
    expect(firstNameFromEmail('')).toBe('there')
    expect(firstNameFromEmail(null)).toBe('there')
  })

  it('greets by the clock', () => {
    expect(greeting(new Date(2026, 8, 10, 9))).toBe('Good morning')
    expect(greeting(new Date(2026, 8, 10, 14))).toBe('Good afternoon')
    expect(greeting(new Date(2026, 8, 10, 21))).toBe('Good evening')
  })
})
