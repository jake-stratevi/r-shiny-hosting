import { describe, expect, it } from 'vitest'
import { MAX_ZIP_BYTES } from '../api/types'
import {
  checkZip,
  emptyDraft,
  humanSize,
  packagesFrom,
  stepBlocker,
  suggestKey,
  toCreateBody,
  type CreateDraft,
  type KeyCheck,
} from './createDraft'

const OK_KEY: KeyCheck = {
  state: 'ok',
  hostPreview: 'access-atlas-xxxxxx.tools.stratevi.com',
  suffixChars: 6,
  reason: null,
}

/** A draft that has cleared everything up to (but not including) `stop`. */
function draft(overrides: Partial<CreateDraft> = {}): CreateDraft {
  return {
    ...emptyDraft(),
    label: 'Access Atlas',
    key: 'access-atlas',
    uploadPhase: 'ready',
    uploadKey: 'uploads/abc.zip',
    file: fakeZip('app.zip', 2048),
    packagesText: 'shiny\ndplyr',
    packagesConfirmed: true,
    allowed_emails: ['jake@stratevi.com'],
    ...overrides,
  }
}

function fakeZip(name: string, size: number): File {
  const file = new File(['x'], name, { type: 'application/zip' })
  Object.defineProperty(file, 'size', { value: size })
  return file
}

describe('suggestKey', () => {
  it('turns a label into a hostname-safe slug', () => {
    expect(suggestKey('Treatment Pathway Dashboard')).toBe('treatment-pathway-dashboard')
    expect(suggestKey('  Q2 Payer Survey!  ')).toBe('q2-payer-survey')
  })

  it('never leaves a trailing hyphen after the 30-char clip', () => {
    const key = suggestKey('A very long label indeed yes x')
    expect(key.length).toBeLessThanOrEqual(30)
    expect(key.endsWith('-')).toBe(false)
  })
})

describe('checkZip', () => {
  it('accepts a normal bundle', () => {
    expect(checkZip(fakeZip('app.zip', 5 * 1024 * 1024))).toBeNull()
  })

  it('rejects anything that is not a .zip', () => {
    expect(checkZip(fakeZip('app.tar.gz', 1024))?.message).toMatch(/not a \.zip/i)
  })

  it('rejects an empty file', () => {
    expect(checkZip(fakeZip('app.zip', 0))?.message).toMatch(/empty/i)
  })

  it('rejects a bundle over the 100 MB cap, before any presigned URL is asked for', () => {
    const problem = checkZip(fakeZip('app.zip', MAX_ZIP_BYTES + 1))
    expect(problem?.message).toMatch(/over the 100 MB limit/)
    // And says what to do about it, rather than just "too big".
    expect(problem?.message).toMatch(/S3/)
  })

  it('accepts a bundle exactly at the cap', () => {
    expect(checkZip(fakeZip('app.zip', MAX_ZIP_BYTES))).toBeNull()
  })
})

describe('packagesFrom', () => {
  it('splits on newlines, commas and spaces, and de-duplicates', () => {
    expect(packagesFrom(draft({ packagesText: 'shiny\ndplyr, shiny  DT\n\n' }))).toEqual([
      'shiny',
      'dplyr',
      'DT',
    ])
  })

  it('is empty for an empty box', () => {
    expect(packagesFrom(draft({ packagesText: '   \n ' }))).toEqual([])
  })
})

describe('stepBlocker: details', () => {
  it('wants a label', () => {
    expect(stepBlocker('details', draft({ label: '  ' }), OK_KEY)).toMatch(/label/i)
  })

  it('wants a key', () => {
    expect(stepBlocker('details', draft({ key: '' }), OK_KEY)).toMatch(/key/i)
  })

  it('blocks while the key is still being checked', () => {
    const check: KeyCheck = { state: 'checking', hostPreview: null, suffixChars: null, reason: null }
    expect(stepBlocker('details', draft(), check)).toMatch(/checking/i)
  })

  it('repeats the API’s rejection verbatim', () => {
    const reason = 'That name can’t be used in a public hostname — pick a project codename.'
    const check: KeyCheck = { state: 'rejected', hostPreview: null, suffixChars: null, reason }
    expect(stepBlocker('details', draft(), check)).toBe(reason)
  })

  it('blocks when the key could not be checked at all', () => {
    const check: KeyCheck = { state: 'unknown', hostPreview: null, suffixChars: null, reason: 'offline' }
    expect(stepBlocker('details', draft(), check)).toMatch(/could not be checked/i)
  })

  it('passes a labelled app with an available key', () => {
    expect(stepBlocker('details', draft(), OK_KEY)).toBeNull()
  })
})

describe('stepBlocker: upload', () => {
  it('wants a file', () => {
    expect(
      stepBlocker('upload', draft({ file: null, uploadPhase: 'empty' }), OK_KEY),
    ).toMatch(/\.zip/)
  })

  it('surfaces the upload’s own error', () => {
    const blocked = draft({ uploadPhase: 'failed', uploadError: 'S3 refused it.' })
    expect(stepBlocker('upload', blocked, OK_KEY)).toBe('S3 refused it.')
  })

  it('waits for the upload to land', () => {
    const busy = draft({ uploadPhase: 'uploading', uploadKey: null })
    expect(stepBlocker('upload', busy, OK_KEY)).toMatch(/wait/i)
  })

  it('never lets an empty package list through', () => {
    expect(stepBlocker('upload', draft({ packagesText: '' }), OK_KEY)).toMatch(
      /packages/i,
    )
  })

  it('requires the list to be explicitly confirmed, never assumed', () => {
    expect(stepBlocker('upload', draft({ packagesConfirmed: false }), OK_KEY)).toMatch(
      /confirm/i,
    )
  })

  it('passes a confirmed bundle', () => {
    expect(stepBlocker('upload', draft(), OK_KEY)).toBeNull()
  })
})

describe('stepBlocker: access', () => {
  it('refuses an empty allowlist under `users`', () => {
    const shut = draft({ access_mode: 'users', allowed_emails: [] })
    expect(stepBlocker('access', shut, OK_KEY)).toMatch(/locks everybody out/)
  })

  it('does not ask for addresses under `all_users`', () => {
    const open = draft({ access_mode: 'all_users', allowed_emails: [] })
    expect(stepBlocker('access', open, OK_KEY)).toBeNull()
  })

  it('holds idle_minutes to 1–1440', () => {
    expect(stepBlocker('access', draft({ idle_minutes: '0' }), OK_KEY)).toMatch(/1440/)
    expect(stepBlocker('access', draft({ idle_minutes: '1441' }), OK_KEY)).toMatch(/1440/)
    expect(stepBlocker('access', draft({ idle_minutes: '7.5' }), OK_KEY)).toMatch(/whole/)
    expect(stepBlocker('access', draft({ idle_minutes: '' }), OK_KEY)).toMatch(/1440/)
  })

  it('holds max_session_hours to 0–168, with 0 allowed', () => {
    expect(stepBlocker('access', draft({ max_session_hours: '0' }), OK_KEY)).toBeNull()
    expect(stepBlocker('access', draft({ max_session_hours: '169' }), OK_KEY)).toMatch(
      /168/,
    )
  })
})

describe('stepBlocker: expiry has no default', () => {
  it('blocks until the user has actually chosen', () => {
    const undecided = draft({ expiryChosen: false, expires_at: null })
    expect(stepBlocker('expiry', undecided, OK_KEY)).toMatch(/no default/i)
  })

  it('accepts an explicit never', () => {
    expect(
      stepBlocker('expiry', draft({ expiryChosen: true, expires_at: null }), OK_KEY),
    ).toBeNull()
  })

  it('accepts a date', () => {
    const dated = draft({ expiryChosen: true, expires_at: 1_800_000_000 })
    expect(stepBlocker('expiry', dated, OK_KEY)).toBeNull()
  })

  it('is re-checked at review, because null alone cannot prove a choice', () => {
    const undecided = draft({ expiryChosen: false, expires_at: null })
    expect(stepBlocker('review', undecided, OK_KEY)).toMatch(/expiry/i)
  })
})

describe('stepBlocker: review re-runs every earlier step', () => {
  it('reports the earliest problem, not the last', () => {
    const broken = draft({ label: '', allowed_emails: [], expiryChosen: false })
    expect(stepBlocker('review', broken, OK_KEY)).toMatch(/label/i)
  })

  it('is clear when everything is', () => {
    expect(stepBlocker('review', draft({ expiryChosen: true }), OK_KEY)).toBeNull()
  })
})

describe('toCreateBody', () => {
  it('maps the two allowed sizes to Fargate units', () => {
    expect(toCreateBody(draft({ size: 'dashboard' }))).toMatchObject({
      cpu: 512,
      memory: 2048,
    })
    expect(toCreateBody(draft({ size: 'model' }))).toMatchObject({
      cpu: 4096,
      memory: 16384,
    })
  })

  it('always carries expires_at, including the null that means never', () => {
    const body = toCreateBody(draft({ expiryChosen: true, expires_at: null }))
    expect('expires_at' in body).toBe(true)
    expect(body.expires_at).toBeNull()
  })

  it('drops the allowlist when the mode does not use it', () => {
    const body = toCreateBody(draft({ access_mode: 'all_users' }))
    expect(body.allowed_emails).toEqual([])
  })

  it('sends numbers, not the strings the inputs hold', () => {
    const body = toCreateBody(draft({ idle_minutes: '10', max_session_hours: '0' }))
    expect(body.idle_minutes).toBe(10)
    expect(body.max_session_hours).toBe(0)
  })

  it('sends the confirmed package list', () => {
    expect(toCreateBody(draft()).packages).toEqual(['shiny', 'dplyr'])
  })
})

describe('humanSize', () => {
  it('scales the unit to the number', () => {
    expect(humanSize(512)).toBe('512 B')
    expect(humanSize(2048)).toBe('2 KB')
    expect(humanSize(5 * 1024 * 1024)).toBe('5.0 MB')
  })
})
