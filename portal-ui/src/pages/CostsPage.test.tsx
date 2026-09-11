import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import type { AppCost, CostPeriod, CostsReport } from '../api/types'
import { clientMe, renderPage } from '../test/render'
import { CostsPage } from './CostsPage'

const costs = vi.hoisted(() => vi.fn())
vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return { ...actual, api: { ...actual.api, costs } }
})

const NOW = Math.floor(Date.now() / 1000)

function appCost(overrides: Partial<AppCost> = {}): AppCost {
  return {
    host: 'dashboard.tools.stratevi.com',
    label: 'Treatment Pathway Dashboard',
    app_key: 'dashboard',
    cpu: 512,
    memory: 2048,
    size_known: true,
    hourly_rate: 0.02913,
    awake_hours: 12,
    estimated_cost: 0.35,
    last_run: NOW - 3600,
    currently_awake: false,
    daily: [{ day: '2026-09-02', awake_hours: 12 }],
    anomalies: {},
    ...overrides,
  }
}

function period(overrides: Partial<CostPeriod> = {}): CostPeriod {
  const apps = overrides.apps ?? [appCost()]
  return {
    month: '2026-09',
    start: 1_788_307_200,
    end: 1_790_899_200,
    complete: false,
    apps,
    apps_total: 0.35,
    overhead: {
      shared: true,
      lines: [
        {
          name: 'Application Load Balancer',
          monthly: 16.43,
          note: 'Always on. $0.0225/hr x 730.',
        },
        {
          name: 'Authorizing proxy task',
          monthly: 9.01,
          note: '0.25 vCPU / 512 MB, always on.',
        },
      ],
      monthly_total: 29.94,
      to_date_total: 15.47,
      elapsed_fraction: 0.5167,
      note: 'Shared by every app and attributable to none.',
    },
    total: 15.82,
    ...overrides,
  }
}

function report(overrides: Partial<CostsReport> = {}): CostsReport {
  return {
    currency: 'USD',
    basis: 'awake_time',
    generated_at: NOW,
    stale: false,
    rates: {
      vcpu_hour: 0.04048,
      gb_hour: 0.004445,
      region: 'us-east-1',
      source: 'AWS Fargate on-demand pricing, Linux/X86, us-east-1.',
    },
    disclaimer:
      'Estimates derived from recorded awake time and published Fargate rates, ' +
      'not billed amounts. AWS Cost Explorer remains the source of truth for an invoice.',
    month_to_date: period(),
    previous_month: period({
      month: '2026-08',
      complete: true,
      apps: [appCost({ awake_hours: 40, estimated_cost: 1.17 })],
      apps_total: 1.17,
      total: 31.11,
    }),
    ...overrides,
  }
}

beforeEach(() => {
  costs.mockReset()
})

describe('CostsPage', () => {
  it('renders per-app awake hours, estimated cost and last run', async () => {
    costs.mockResolvedValue(report())
    renderPage(<CostsPage />)

    const row = await screen.findByRole('row', { name: /Treatment Pathway Dashboard/ })
    expect(within(row).getByText('12h')).toBeInTheDocument()
    expect(within(row).getByText('$0.35')).toBeInTheDocument()
    expect(within(row).getByText(/ago/)).toBeInTheDocument()
    expect(within(row).getByText(/0\.5 vCPU \/ 2 GB/)).toBeInTheDocument()
  })

  it('says plainly that these are estimates, not billed amounts', async () => {
    costs.mockResolvedValue(report())
    renderPage(<CostsPage />)

    expect(
      await screen.findByText('These are estimates, not billed amounts'),
    ).toBeInTheDocument()
    expect(screen.getByText(/Cost Explorer remains the source of truth/)).toBeInTheDocument()
  })

  it('shows the shared overhead as its own line, itemised', async () => {
    costs.mockResolvedValue(report())
    renderPage(<CostsPage />)

    expect(await screen.findByText('Shared platform, itemised')).toBeInTheDocument()
    expect(screen.getByText('Application Load Balancer')).toBeInTheDocument()
    expect(screen.getByText('Authorizing proxy task')).toBeInTheDocument()
    expect(screen.getByText(/Shared by every app and attributable to none/)).toBeInTheDocument()
  })

  it('never divides overhead into the per-app rows', async () => {
    costs.mockResolvedValue(report())
    renderPage(<CostsPage />)

    const row = await screen.findByRole('row', { name: /Treatment Pathway Dashboard/ })
    // The app's own compute, and nothing resembling a share of $29.94.
    expect(within(row).queryByText(/29\.94/)).not.toBeInTheDocument()
    expect(within(row).queryByText(/5\.98/)).not.toBeInTheDocument()
  })

  it('draws no chart, because there is no data for one', async () => {
    costs.mockResolvedValue(report())
    const { container } = renderPage(<CostsPage />)
    await screen.findByText('Shared platform, itemised')
    // Icons are decorative SVGs; a chart would be a canvas or an svg with a
    // role. Neither exists here, on purpose.
    expect(container.querySelector('canvas')).toBeNull()
    expect(container.querySelector('svg[role="img"]')).toBeNull()
  })

  it('switches to the previous full month', async () => {
    costs.mockResolvedValue(report())
    renderPage(<CostsPage />)

    await userEvent.click(await screen.findByRole('tab', { name: 'August 2026' }))
    const row = await screen.findByRole('row', { name: /Treatment Pathway Dashboard/ })
    expect(within(row).getByText('40h')).toBeInTheDocument()
    expect(within(row).getByText('$1.17')).toBeInTheDocument()
  })

  it('pro-rates the shared line for a month in progress, and says so', async () => {
    costs.mockResolvedValue(report())
    renderPage(<CostsPage />)

    await screen.findByText('Shared platform, itemised')
    // Once in the tile, once as the section's running figure.
    expect(screen.getAllByText('$15.47').length).toBe(2)
    expect(screen.getByText(/pro-rated to the 52%/)).toBeInTheDocument()
  })

  it('shows the full monthly figure for a completed month', async () => {
    costs.mockResolvedValue(report())
    renderPage(<CostsPage />)

    await userEvent.click(await screen.findByRole('tab', { name: 'August 2026' }))
    expect(screen.queryByText(/pro-rated to the/)).not.toBeInTheDocument()
    expect(screen.getAllByText('$29.94').length).toBeGreaterThan(0)
  })

  // --- zero and partial data -------------------------------------------------

  it('renders with no apps at all', async () => {
    costs.mockResolvedValue(
      report({
        month_to_date: period({ apps: [], apps_total: 0, total: 15.47 }),
      }),
    )
    renderPage(<CostsPage />)

    expect(await screen.findByText('No apps to cost')).toBeInTheDocument()
    // The shared line still applies and is still shown.
    expect(screen.getByText('Shared platform, itemised')).toBeInTheDocument()
  })

  it('renders an app that was never awake without pretending it cost something', async () => {
    costs.mockResolvedValue(
      report({
        month_to_date: period({
          apps: [
            appCost({ awake_hours: 0, estimated_cost: 0, last_run: null, daily: [] }),
          ],
          apps_total: 0,
        }),
      }),
    )
    renderPage(<CostsPage />)

    const row = await screen.findByRole('row', { name: /Treatment Pathway Dashboard/ })
    expect(within(row).getByText('0h')).toBeInTheDocument()
    expect(within(row).getByText('$0.00')).toBeInTheDocument()
    expect(within(row).getByText('never')).toBeInTheDocument()
    expect(screen.getByText(/No app was awake in this period/)).toBeInTheDocument()
  })

  it('reports an unknown task size instead of guessing a cost', async () => {
    costs.mockResolvedValue(
      report({
        month_to_date: period({
          apps: [
            appCost({
              label: 'Hand-provisioned thing',
              size_known: false,
              hourly_rate: null,
              cpu: 0,
              memory: 0,
              awake_hours: 9,
              estimated_cost: 0,
            }),
          ],
          apps_total: 0,
        }),
      }),
    )
    renderPage(<CostsPage />)

    const row = await screen.findByRole('row', { name: /Hand-provisioned thing/ })
    expect(within(row).getByText(/Task size unknown/)).toBeInTheDocument()
    expect(within(row).getByText('9.0h')).toBeInTheDocument()
    expect(within(row).getByText('—')).toBeInTheDocument()
  })

  it('surfaces a capped run rather than hiding it', async () => {
    costs.mockResolvedValue(
      report({
        month_to_date: period({
          apps: [appCost({ anomalies: { unclosed: 2 } })],
        }),
      }),
    )
    renderPage(<CostsPage />)

    expect(
      await screen.findByText(/2 runs never recorded stopping and were capped/),
    ).toBeInTheDocument()
  })

  it('flags an app that is awake right now', async () => {
    costs.mockResolvedValue(
      report({
        month_to_date: period({ apps: [appCost({ currently_awake: true })] }),
      }),
    )
    renderPage(<CostsPage />)
    expect(await screen.findByText(/Awake now/)).toBeInTheDocument()
  })

  it('warns when the ledger could not be read', async () => {
    costs.mockResolvedValue(report({ stale: true }))
    renderPage(<CostsPage />)
    expect(
      await screen.findByText('Some usage data could not be read'),
    ).toBeInTheDocument()
  })

  it('survives a period that arrives without its apps array', async () => {
    costs.mockResolvedValue(
      report({
        // A backend mid-deploy, or a 200 with half a payload.
        month_to_date: { ...period(), apps: undefined as never },
      }),
    )
    renderPage(<CostsPage />)
    expect(await screen.findByText('No apps to cost')).toBeInTheDocument()
  })

  it('survives a period with no overhead block', async () => {
    costs.mockResolvedValue(
      report({
        month_to_date: { ...period(), overhead: undefined as never },
      }),
    )
    renderPage(<CostsPage />)
    expect(await screen.findByText('Shared platform, itemised')).toBeInTheDocument()
  })

  // --- failure ---------------------------------------------------------------

  it('shows the 503 message when cost reporting is not configured', async () => {
    costs.mockRejectedValue(
      new ApiError(503, 'cost reporting is not configured on this deployment'),
    )
    renderPage(<CostsPage />)
    expect(
      await screen.findByText('cost reporting is not configured on this deployment'),
    ).toBeInTheDocument()
  })

  it('shows the no-admin state on a 403', async () => {
    costs.mockRejectedValue(new ApiError(403, 'you are not a platform administrator'))
    renderPage(<CostsPage />, { me: clientMe })
    expect(await screen.findByText(/don.t have admin access/i)).toBeInTheDocument()
  })
})
