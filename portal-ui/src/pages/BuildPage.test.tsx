import { act, screen, waitFor } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import type { BuildStatus } from '../api/types'
import { creatorMe, renderPage } from '../test/render'
import { BuildPage, formatElapsed } from './BuildPage'

const build = vi.hoisted(() => vi.fn())
vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return { ...actual, api: { ...actual.api, build } }
})

const HOST = 'access-atlas.tools.stratevi.com'
const CONSOLE_URL = 'https://console.aws.amazon.com/codesuite/codebuild/whatever'

function status(overrides: Partial<BuildStatus> = {}): BuildStatus {
  return {
    state: 'building',
    phase: 'BUILD',
    started_at: Math.floor(Date.now() / 1000) - 185,
    elapsed_s: 185,
    log_url: CONSOLE_URL,
    log_tail: ['* installing *source* package ‘dplyr’ ...', '* DONE (dplyr)'],
    ...overrides,
  }
}

/** The build page plus a sentinel for the detail route success redirects to. */
function renderBuild(me = undefined as Parameters<typeof renderPage>[1]) {
  return renderPage(
    <Routes>
      <Route path="/admin/apps/:host/build" element={<BuildPage />} />
      <Route path="/admin/apps/:host" element={<p>App settings</p>} />
    </Routes>,
    { route: `/admin/apps/${HOST}/build`, ...me },
  )
}

beforeEach(() => {
  build.mockReset()
  build.mockResolvedValue(status())
})

afterEach(() => {
  vi.useRealTimers()
})

describe('BuildPage — building', () => {
  it('names the phase, the elapsed time and why it takes so long', async () => {
    renderBuild()

    expect(await screen.findByText(/Phase:/)).toHaveTextContent('BUILD')
    expect(screen.getByText(/First builds take 10–20 minutes/)).toBeInTheDocument()
    expect(screen.getByText('3m 05s')).toBeInTheDocument()
    expect(screen.getByText('Building')).toBeInTheDocument()
  })

  it('does not fake a progress bar', async () => {
    renderBuild()

    await screen.findByText(/Phase:/)
    // The spec is explicit about this, so the test is too.
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
    expect(screen.queryByText(/%/)).not.toBeInTheDocument()
    expect(screen.getByText(/no progress bar/i)).toBeInTheDocument()
  })

  it('shows the log tail and a deep link to the console', async () => {
    renderBuild()

    expect(await screen.findByLabelText('Build log tail')).toHaveTextContent(
      '* DONE (dplyr)',
    )
    expect(screen.getByRole('link', { name: /Console/ })).toHaveAttribute(
      'href',
      CONSOLE_URL,
    )
  })

  it('says so when CodeBuild has produced no output yet', async () => {
    build.mockResolvedValue(status({ log_tail: [] }))
    renderBuild()

    expect(await screen.findByText(/No output yet/)).toBeInTheDocument()
  })
})

describe('BuildPage — transitions', () => {
  it('polls, and sends an admin to the app’s settings once the build succeeds', async () => {
    vi.useFakeTimers()
    build
      .mockResolvedValueOnce(status({ phase: 'INSTALL' }))
      .mockResolvedValue(status({ state: 'succeeded', phase: 'COMPLETED' }))

    renderBuild()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(screen.getByText(/Phase:/)).toHaveTextContent('INSTALL')

    // One poll interval later the build has landed.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_100)
    })
    expect(screen.getByText('App settings')).toBeInTheDocument()
  })

  it('gives a creator who is not an admin the success state instead of a 403', async () => {
    build.mockResolvedValue(status({ state: 'succeeded', phase: 'COMPLETED' }))
    renderBuild({ me: creatorMe })

    expect(await screen.findByText("It's live")).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Open the app/ })).toHaveAttribute(
      'href',
      `https://${HOST}`,
    )
    expect(screen.queryByText('App settings')).not.toBeInTheDocument()
  })

  it('shows the failure, the log tail and a way forward', async () => {
    build.mockResolvedValue(
      status({
        state: 'failed',
        phase: 'BUILD',
        elapsed_s: 18 * 60,
        log_tail: ['Error: installation of package ‘rstan’ had non-zero exit status'],
      }),
    )
    renderBuild()

    expect(await screen.findByText('The build failed')).toBeInTheDocument()
    expect(screen.getByText('Build failed')).toBeInTheDocument()
    expect(screen.getByLabelText('Build log tail')).toHaveTextContent('rstan')
    expect(
      screen.getByRole('link', { name: /Start over with a fixed bundle/ }),
    ).toHaveAttribute('href', '/admin/apps/new')
    // Nothing was torn down, and the page says so.
    expect(screen.getByText(/kept on purpose/)).toBeInTheDocument()
  })

  it('stops polling once the build is no longer running', async () => {
    vi.useFakeTimers()
    build.mockResolvedValue(status({ state: 'failed' }))
    renderBuild()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    const calls = build.mock.calls.length

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000)
    })
    expect(build.mock.calls.length).toBe(calls)
  })

  it('renders the API’s error rather than an empty page', async () => {
    build.mockRejectedValue(new ApiError(404, 'No build is recorded for that host.'))
    renderBuild()

    await waitFor(() =>
      expect(
        screen.getByText('No build is recorded for that host.'),
      ).toBeInTheDocument(),
    )
  })
})

describe('formatElapsed', () => {
  it('reads as a stopwatch, then as hours', () => {
    expect(formatElapsed(0)).toBe('0m 00s')
    expect(formatElapsed(65)).toBe('1m 05s')
    expect(formatElapsed(19 * 60)).toBe('19m 00s')
    expect(formatElapsed(3 * 3600 + 7 * 60)).toBe('3h 07m')
  })
})
