import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, CSRF_HEADER, api, request } from './client'

type FetchArgs = [string, RequestInit]

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

let fetchMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  fetchMock = vi.fn()
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const lastCall = (): FetchArgs => fetchMock.mock.calls.at(-1) as FetchArgs
const headersOf = (init: RequestInit) => init.headers as Record<string, string>

describe('CSRF header', () => {
  it('is not sent on GETs', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ email: 'a@b.com', is_admin: true }))
    await api.me()
    const [, init] = lastCall()
    expect(init.method).toBe('GET')
    expect(headersOf(init)[CSRF_HEADER]).toBeUndefined()
  })

  it('is sent on PATCH, with the JSON body and same-origin credentials', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ host: 'a.example.com' }))
    await api.patchApp('a.example.com', { idle_minutes: 20 })

    const [url, init] = lastCall()
    expect(url).toBe('/api/v1/apps/a.example.com')
    expect(init.method).toBe('PATCH')
    expect(headersOf(init)[CSRF_HEADER]).toBe('1')
    expect(headersOf(init)['Content-Type']).toBe('application/json')
    expect(init.credentials).toBe('same-origin')
    expect(JSON.parse(String(init.body))).toEqual({ idle_minutes: 20 })
  })

  it('is sent on any non-safe method', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }))
    await request('/api/v1/anything', { method: 'post', body: {} })
    expect(headersOf(lastCall()[1])[CSRF_HEADER]).toBe('1')
    expect(lastCall()[1].method).toBe('POST')
  })
})

describe('error mapping', () => {
  it('surfaces the {error} message from the body', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ error: 'idle_minutes must be 1-1440.' }, 400))
    const err = await api.app('a.example.com').catch((e: unknown) => e)

    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(400)
    expect((err as ApiError).message).toBe('idle_minutes must be 1-1440.')
  })

  it('flags 403 so the UI can render the no-admin-access state', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ error: 'Admin access is required.' }, 403))
    const err = (await api.apps().catch((e: unknown) => e)) as ApiError

    expect(err.isForbidden).toBe(true)
    expect(err.isNotFound).toBe(false)
    expect(err.message).toBe('Admin access is required.')
  })

  it('falls back to a readable message when the body has no {error}', async () => {
    fetchMock.mockResolvedValue(new Response('<html>gateway</html>', { status: 502 }))
    const err = (await api.me().catch((e: unknown) => e)) as ApiError

    expect(err.status).toBe(502)
    expect(err.message).toMatch(/502/)
  })

  it('maps 404 with an empty body', async () => {
    fetchMock.mockResolvedValue(new Response('', { status: 404 }))
    const err = (await api.app('nope.example.com').catch((e: unknown) => e)) as ApiError

    expect(err.isNotFound).toBe(true)
    expect(err.message).toBe('Not found.')
  })

  it('turns a network failure into a status-0 ApiError', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))
    const err = (await api.me().catch((e: unknown) => e)) as ApiError

    expect(err.isNetwork).toBe(true)
    expect(err.message).toMatch(/reach the portal service/i)
  })

  it('rethrows aborts untouched so unmounts stay quiet', async () => {
    fetchMock.mockRejectedValue(new DOMException('aborted', 'AbortError'))
    const err = await api.me().catch((e: unknown) => e)

    expect(err).toBeInstanceOf(DOMException)
    expect(err).not.toBeInstanceOf(ApiError)
  })
})

describe('shapes', () => {
  it('unwraps the menu envelope', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ apps: [{ host: 'a.example.com', live_state: 'asleep' }] }),
    )
    await expect(api.menu()).resolves.toHaveLength(1)
  })

  it('accepts GET /apps as a bare array or as {apps: [...]}', async () => {
    fetchMock.mockResolvedValue(jsonResponse([{ host: 'a.example.com' }]))
    await expect(api.apps()).resolves.toHaveLength(1)

    fetchMock.mockResolvedValue(jsonResponse({ apps: [{ host: 'a' }, { host: 'b' }] }))
    await expect(api.apps()).resolves.toHaveLength(2)
  })

  it('builds the audit query string and omits an absent cursor', async () => {
    // A Response body can only be read once, so build a fresh one per call.
    fetchMock.mockImplementation(async () => jsonResponse({ events: [], cursor: null }))

    await api.audit('a.example.com', { limit: 50 })
    expect(lastCall()[0]).toBe('/api/v1/apps/a.example.com/audit?limit=50')

    await api.audit('a.example.com', { limit: 50, cursor: 'abc==' })
    expect(lastCall()[0]).toBe('/api/v1/apps/a.example.com/audit?limit=50&cursor=abc%3D%3D')
  })
})
