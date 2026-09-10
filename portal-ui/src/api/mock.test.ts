// mock.ts is dev-only, but it is what `npm run dev:mock` demos the wizard
// with, so the P2a routes are worth a smoke test: a mock that lies is worse
// than no mock.

import { beforeEach, describe, expect, it } from 'vitest'
import { mockFetch } from './mock'

const POST = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'X-Portal-Csrf': '1' },
  body: JSON.stringify(body),
})

const readJson = async (res: Response) => (await res.json()) as Record<string, unknown>

beforeEach(() => {
  globalThis.__portalMock?.setAdmin(true)
  globalThis.__portalMock?.setCreator(true)
})

describe('mock: /me', () => {
  it('reports can_create alongside is_admin', async () => {
    const me = await readJson(await mockFetch('/api/v1/me'))
    expect(me).toMatchObject({ is_admin: true, can_create: true })
  })
})

describe('mock: validate-key', () => {
  const check = async (key: string) =>
    readJson(await mockFetch('/api/v1/apps/validate-key', POST({ key })))

  it('answers 200 either way — it is a form affordance, not an error', async () => {
    const res = await mockFetch('/api/v1/apps/validate-key', POST({ key: 'nope!' }))
    expect(res.status).toBe(200)
  })

  it('accepts a well-formed, unused key and returns the hostname', async () => {
    expect(await check('q3-uptake')).toEqual({
      ok: true,
      host: 'q3-uptake.tools.stratevi.com',
    })
  })

  it('polices shape', async () => {
    expect(await check('ab')).toMatchObject({ ok: false })
    expect(await check('Bad_Key')).toMatchObject({ ok: false })
    expect(await check('-lead')).toMatchObject({ ok: false })
    expect(await check('dou--ble')).toMatchObject({ ok: false })
  })

  it('refuses reserved names and existing keys', async () => {
    expect((await check('admin')).reason).toMatch(/reserved/)
    expect((await check('dashboard')).reason).toMatch(/already uses/)
  })

  it('refuses a denylisted term without saying which term it was', async () => {
    const reason = String((await check('tarpeyo-uptake')).reason)
    expect(reason).toMatch(/project codename/)
    expect(reason).not.toMatch(/tarpeyo/i)
  })
})

describe('mock: uploads', () => {
  it('refuses an over-size bundle before issuing a URL', async () => {
    const res = await mockFetch(
      '/api/v1/uploads',
      POST({ filename: 'app.zip', size: 200 * 1024 * 1024 }),
    )
    expect(res.status).toBe(400)
  })

  it('issues a presigned PUT for an acceptable one', async () => {
    const ticket = await readJson(
      await mockFetch('/api/v1/uploads', POST({ filename: 'app.zip', size: 1024 })),
    )
    expect(ticket.upload_key).toMatch(/^uploads\/.+\.zip$/)
    expect(ticket.expires_in).toBe(900)
  })

  it('has no inspect route — the bundle is read in the browser', async () => {
    // portal-api.md: "Bundle inspection is CLIENT-SIDE — there is no inspect
    // endpoint". A mock that answered one would hide a broken client.
    const res = await mockFetch(
      '/api/v1/uploads/inspect',
      POST({ upload_key: 'uploads/a.zip' }),
    )
    expect(res.status).toBe(404)
  })
})

describe('mock: create + build', () => {
  const create = (over: Record<string, unknown> = {}) =>
    mockFetch(
      '/api/v1/apps',
      POST({
        key: 'smoke-test',
        label: 'Smoke Test',
        description: '',
        cpu: 512,
        memory: 2048,
        upload_key: 'uploads/a.zip',
        access_mode: 'all_users',
        allowed_emails: [],
        idle_minutes: 20,
        max_session_hours: 12,
        expires_at: null,
        packages: ['shiny'],
        ...over,
      }),
    )

  it('answers 202 with a building app', async () => {
    const res = await create({ key: 'smoke-one' })
    expect(res.status).toBe(202)
    expect(await readJson(res)).toMatchObject({
      host: 'smoke-one.tools.stratevi.com',
      status: 'building',
      live_state: 'building',
      desired_count: 0,
    })
  })

  it('answers 409 when the key is already taken', async () => {
    expect((await create({ key: 'dashboard' })).status).toBe(409)
  })

  it('refuses a body with no expires_at at all', async () => {
    const res = await mockFetch(
      '/api/v1/apps',
      POST({
        key: 'smoke-two',
        cpu: 512,
        memory: 2048,
        upload_key: 'uploads/a.zip',
        packages: ['shiny'],
      }),
    )
    expect(res.status).toBe(400)
    expect((await readJson(res)).error).toMatch(/expires_at/)
  })

  it('walks the phases and lands on success', async () => {
    await create({ key: 'smoke-three' })
    const poll = async () =>
      readJson(await mockFetch('/api/v1/apps/smoke-three.tools.stratevi.com/build'))

    const first = await poll()
    expect(first.state).toBe('building')
    expect(typeof first.phase).toBe('string')

    let last = first
    for (let i = 0; i < 12 && last.state === 'building'; i++) last = await poll()
    expect(last.state).toBe('succeeded')
  })

  it('takes the failure path for a key that says so', async () => {
    await create({ key: 'smoke-fail' })
    const poll = async () =>
      readJson(await mockFetch('/api/v1/apps/smoke-fail.tools.stratevi.com/build'))

    let last = await poll()
    for (let i = 0; i < 12 && last.state === 'building'; i++) last = await poll()
    expect(last.state).toBe('failed')
    expect(String((last.log_tail as string[]).join('\n'))).toMatch(/non-zero exit status/)
  })
})

describe('mock: creation is its own permission', () => {
  it('403s an admin who is not a creator, on every creation route', async () => {
    globalThis.__portalMock?.setCreator(false)

    for (const [path, init] of [
      ['/api/v1/apps/validate-key', POST({ key: 'x' })],
      ['/api/v1/uploads', POST({ filename: 'a.zip', size: 1 })],
      ['/api/v1/apps', POST({ key: 'x' })],
      ['/api/v1/apps/a.tools.stratevi.com/build', {}],
    ] as Array<[string, RequestInit]>) {
      expect((await mockFetch(path, init)).status).toBe(403)
    }

    // …while the admin routes still work for them.
    expect((await mockFetch('/api/v1/apps')).status).toBe(200)
  })
})
