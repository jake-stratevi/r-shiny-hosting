import type {
  App,
  AppCostsReport,
  AppPatch,
  AuditPage,
  BuildStatus,
  CostsReport,
  CreateAppBody,
  Me,
  MenuApp,
  MenuResponse,
  UploadTicket,
  ValidateKeyResult,
} from './types'

export const API_BASE = '/api/v1'

/** Required on every mutating request. See portal-api.md "CSRF". */
export const CSRF_HEADER = 'X-Portal-Csrf'
export const CSRF_VALUE = '1'

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS'])

/**
 * Every non-2xx from the portal API. `message` is the server's `{"error": ...}`
 * string when there is one, so it can be shown to the user verbatim.
 */
export class ApiError extends Error {
  readonly status: number
  readonly body: unknown

  constructor(status: number, message: string, body?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.body = body
  }

  /** Not an admin (or the row is not visible to this caller). */
  get isForbidden(): boolean {
    return this.status === 403
  }

  get isUnauthorized(): boolean {
    return this.status === 401
  }

  get isNotFound(): boolean {
    return this.status === 404
  }

  /** Network failure / DNS / offline — no HTTP status ever arrived. */
  get isNetwork(): boolean {
    return this.status === 0
  }
}

// Statically false in a normal build, so Vite drops the branch below and the
// fixtures never reach production.
const MOCK = import.meta.env.VITE_PORTAL_MOCK === '1'

async function transport(url: string, init: RequestInit): Promise<Response> {
  if (MOCK) {
    const { mockFetch } = await import('./mock')
    return mockFetch(url, init)
  }
  return fetch(url, init)
}

interface RequestOptions {
  method?: string
  body?: unknown
  signal?: AbortSignal
}

export async function request<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const method = (options.method ?? 'GET').toUpperCase()
  const headers: Record<string, string> = { Accept: 'application/json' }

  if (!SAFE_METHODS.has(method)) {
    // Same-origin fetch can set this; a cross-site form cannot.
    headers[CSRF_HEADER] = CSRF_VALUE
  }

  const init: RequestInit = {
    method,
    headers,
    // The ALB's Cognito session cookie is what authenticates us.
    credentials: 'same-origin',
    signal: options.signal,
  }

  if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    init.body = JSON.stringify(options.body)
  }

  let response: Response
  try {
    response = await transport(path, init)
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause
    throw new ApiError(0, 'Could not reach the portal service.', cause)
  }

  if (response.status === 204) return undefined as T

  const raw = await response.text()
  let parsed: unknown = undefined
  if (raw) {
    try {
      parsed = JSON.parse(raw)
    } catch {
      parsed = undefined
    }
  }

  if (!response.ok) {
    throw new ApiError(response.status, errorMessage(response, parsed), parsed)
  }

  if (parsed === undefined) {
    throw new ApiError(
      response.status,
      'The portal service returned a malformed response.',
    )
  }

  return parsed as T
}

function errorMessage(response: Response, parsed: unknown): string {
  if (
    parsed &&
    typeof parsed === 'object' &&
    'error' in parsed &&
    typeof (parsed as { error: unknown }).error === 'string' &&
    (parsed as { error: string }).error.trim() !== ''
  ) {
    return (parsed as { error: string }).error
  }
  return defaultMessage(response.status, response.statusText)
}

function defaultMessage(status: number, statusText: string): string {
  switch (status) {
    case 401:
      return 'Your session has expired. Reload the page to sign in again.'
    case 403:
      return 'You do not have access to this.'
    case 404:
      return 'Not found.'
    case 409:
      return 'That change conflicts with the current state.'
    default:
      return statusText
        ? `Request failed (${status} ${statusText}).`
        : `Request failed (${status}).`
  }
}

/**
 * GET /api/v1/apps is documented as "Array of full app objects" but every
 * other collection in the contract is wrapped (`{"apps": [...]}`). Accept
 * both so a backend choosing either shape works. See README "Contract notes".
 */
function unwrapApps(payload: unknown): App[] {
  if (Array.isArray(payload)) return payload as App[]
  if (payload && typeof payload === 'object' && Array.isArray((payload as MenuResponse).apps)) {
    return (payload as { apps: App[] }).apps
  }
  return []
}

export const api = {
  me(signal?: AbortSignal): Promise<Me> {
    return request<Me>(`${API_BASE}/me`, { signal })
  },

  async menu(signal?: AbortSignal): Promise<MenuApp[]> {
    const payload = await request<MenuResponse>(`${API_BASE}/menu`, { signal })
    return Array.isArray(payload?.apps) ? payload.apps : []
  },

  async apps(signal?: AbortSignal): Promise<App[]> {
    const payload = await request<unknown>(`${API_BASE}/apps`, { signal })
    return unwrapApps(payload)
  },

  app(host: string, signal?: AbortSignal): Promise<App> {
    return request<App>(`${API_BASE}/apps/${encodeURIComponent(host)}`, { signal })
  },

  patchApp(host: string, patch: AppPatch, signal?: AbortSignal): Promise<App> {
    return request<App>(`${API_BASE}/apps/${encodeURIComponent(host)}`, {
      method: 'PATCH',
      body: patch,
      signal,
    })
  },

  audit(
    host: string,
    opts: { limit?: number; cursor?: string | null } = {},
    signal?: AbortSignal,
  ): Promise<AuditPage> {
    const params = new URLSearchParams()
    if (opts.limit) params.set('limit', String(opts.limit))
    if (opts.cursor) params.set('cursor', opts.cursor)
    const qs = params.toString()
    return request<AuditPage>(
      `${API_BASE}/apps/${encodeURIComponent(host)}/audit${qs ? `?${qs}` : ''}`,
      { signal },
    )
  },

  // --- costs ---------------------------------------------------------------

  /**
   * Per-app awake hours and estimated compute cost, plus the shared overhead
   * line. Estimates derived from the audit trail, not billed amounts — the
   * payload carries its own `disclaimer` and the screen shows it.
   */
  costs(signal?: AbortSignal): Promise<CostsReport> {
    return request<CostsReport>(`${API_BASE}/costs`, { signal })
  },

  appCosts(host: string, signal?: AbortSignal): Promise<AppCostsReport> {
    return request<AppCostsReport>(
      `${API_BASE}/apps/${encodeURIComponent(host)}/costs`,
      { signal },
    )
  },

  // --- P2a: creation -------------------------------------------------------

  /** 200 whether or not the key is usable; `ok` carries the verdict. */
  validateKey(key: string, signal?: AbortSignal): Promise<ValidateKeyResult> {
    return request<ValidateKeyResult>(`${API_BASE}/apps/validate-key`, {
      method: 'POST',
      body: { key },
      signal,
    })
  },

  /** Presigned PUT for the zip. Rejects over-size before issuing a URL. */
  createUpload(
    filename: string,
    size: number,
    signal?: AbortSignal,
  ): Promise<UploadTicket> {
    return request<UploadTicket>(`${API_BASE}/uploads`, {
      method: 'POST',
      body: { filename, size },
      signal,
    })
  },

  // No inspect route: the bundle is read in the browser before the upload
  // (src/lib/inspectBundle.ts). portal-api.md, "Bundle inspection is
  // CLIENT-SIDE — there is no inspect endpoint".

  /** 202 with the app object (`status: building`); 409 if the key is taken. */
  createApp(body: CreateAppBody, signal?: AbortSignal): Promise<App> {
    return request<App>(`${API_BASE}/apps`, { method: 'POST', body, signal })
  },

  build(host: string, signal?: AbortSignal): Promise<BuildStatus> {
    return request<BuildStatus>(
      `${API_BASE}/apps/${encodeURIComponent(host)}/build`,
      { signal },
    )
  },
}

export interface UploadOptions {
  /** 0..1. Fired from XHR's real upload events — never interpolated. */
  onProgress?: (fraction: number) => void
  signal?: AbortSignal
}

/**
 * PUT the zip straight to S3 from the browser.
 *
 * `fetch` has no upload-progress event, so this is XHR: a 100 MB bundle over
 * office wifi is a minute of silence otherwise, and silence during an upload
 * reads as a hang.
 *
 * No `Content-Type` is set deliberately — the browser sends the File's own
 * type, and adding an unsigned header to a presigned URL is the classic way
 * to earn a 403. See README "Contract notes" #10.
 */
export function uploadToS3(
  url: string,
  file: File,
  options: UploadOptions = {},
): Promise<void> {
  if (MOCK) {
    return import('./mock').then(({ mockUpload }) => mockUpload(file, options))
  }

  return new Promise<void>((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('PUT', url, true)

    xhr.upload.addEventListener('progress', (event) => {
      if (event.lengthComputable && event.total > 0) {
        options.onProgress?.(event.loaded / event.total)
      }
    })

    xhr.addEventListener('load', () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        options.onProgress?.(1)
        resolve()
        return
      }
      // S3 answers in XML, which is no use to anyone reading a wizard.
      reject(
        new ApiError(
          xhr.status,
          `S3 refused the upload (${xhr.status}). The link may have expired — go back and choose the file again.`,
        ),
      )
    })

    xhr.addEventListener('error', () =>
      reject(new ApiError(0, 'The upload could not reach S3.')),
    )
    xhr.addEventListener('abort', () =>
      reject(new DOMException('Upload aborted', 'AbortError')),
    )

    if (options.signal) {
      if (options.signal.aborted) {
        xhr.abort()
        return
      }
      options.signal.addEventListener('abort', () => xhr.abort(), { once: true })
    }

    xhr.send(file)
  })
}
