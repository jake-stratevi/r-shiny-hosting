import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { App } from '../api/types'
import { adminNoCreateMe, renderPage } from '../test/render'
import { AdminAppsPage } from './AdminAppsPage'

const apps = vi.hoisted(() => vi.fn())
vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return { ...actual, api: { ...actual.api, apps } }
})

const NOW = Math.floor(Date.now() / 1000)

function app(overrides: Partial<App> = {}): App {
  return {
    host: 'dashboard.tools.stratevi.com',
    app_key: 'dashboard',
    label: 'Treatment Pathway Dashboard',
    description: 'Sankey of treatment sequences.',
    ecs_service: 'shiny-dashboard',
    container_port: 3838,
    status: 'active',
    live_state: 'awake',
    access_mode: 'users',
    allowed_emails: ['jake@stratevi.com', 'yi@stratevi.com'],
    idle_minutes: 15,
    max_session_hours: 12,
    expires_at: null,
    last_active: NOW - 180,
    awake_since: NOW - 3600,
    desired_count: 1,
    running_count: 1,
    ...overrides,
  }
}

const rows: App[] = [
  app(),
  app({
    host: 'payer-survey.tools.stratevi.com',
    app_key: 'payer-survey',
    label: 'Payer Survey Explorer',
    description: 'Cross-tabs of the Q2 payer survey.',
    live_state: 'asleep',
    access_mode: 'all_users',
    allowed_emails: [],
    expires_at: NOW + 6 * 86_400,
    desired_count: 0,
    running_count: 0,
  }),
  app({
    host: 'pilot-uptake.tools.stratevi.com',
    app_key: 'pilot-uptake',
    label: 'Uptake Pilot',
    description: 'Two-week client pilot.',
    status: 'expired',
    live_state: 'expired',
    expires_at: NOW - 4 * 86_400,
    desired_count: 0,
    running_count: 0,
  }),
]

beforeEach(() => {
  apps.mockReset()
  apps.mockResolvedValue(rows)
  try {
    localStorage.clear()
  } catch {
    /* ignore */
  }
})

describe('AdminAppsPage', () => {
  it('summarises the fleet in clickable tiles', async () => {
    renderPage(<AdminAppsPage />, { route: '/admin' })

    const awake = await screen.findByRole('button', { name: /Awake now/ })
    expect(within(awake).getByText('1')).toBeInTheDocument()
    // Expired + expiring-within-a-week = two rows wanting a look.
    const attention = screen.getByRole('button', { name: /Needs attention/ })
    expect(within(attention).getByText('2')).toBeInTheDocument()
  })

  it('filters the list when a stat tile is pressed', async () => {
    const user = userEvent.setup()
    renderPage(<AdminAppsPage />, { route: '/admin' })

    await user.click(await screen.findByRole('button', { name: /Awake now/ }))

    expect(screen.getByRole('link', { name: /Treatment Pathway/ })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Uptake Pilot/ })).not.toBeInTheDocument()
    expect(screen.getByText('Showing 1 of 3 apps.')).toBeInTheDocument()
  })

  it('searches across label, host and key', async () => {
    const user = userEvent.setup()
    renderPage(<AdminAppsPage />, { route: '/admin' })

    await user.type(await screen.findByLabelText('Search apps'), 'payer')

    expect(screen.getByRole('link', { name: /Payer Survey/ })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Treatment Pathway/ })).not.toBeInTheDocument()
  })

  it('offers a way out of a search that matches nothing', async () => {
    const user = userEvent.setup()
    renderPage(<AdminAppsPage />, { route: '/admin' })

    await user.type(await screen.findByLabelText('Search apps'), 'zzzz')
    expect(screen.getByText('No apps match.')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Clear search and filters' }))
    expect(screen.getByRole('link', { name: /Treatment Pathway/ })).toBeInTheDocument()
  })

  it('summarises access and expiry per row', async () => {
    renderPage(<AdminAppsPage />, { route: '/admin' })

    await screen.findByRole('link', { name: /Treatment Pathway/ })
    expect(screen.getAllByText('2 people').length).toBe(2)
    expect(screen.getByText('Everyone signed in')).toBeInTheDocument()
    expect(screen.getAllByText('Never').length).toBeGreaterThan(0)
    expect(screen.getByText('Expired')).toBeInTheDocument()
  })

  it('links every row to its detail page', async () => {
    renderPage(<AdminAppsPage />, { route: '/admin' })

    const link = await screen.findByRole('link', { name: /Treatment Pathway/ })
    expect(link).toHaveAttribute(
      'href',
      '/admin/apps/dashboard.tools.stratevi.com',
    )
  })

  it('remembers the card/table view choice', async () => {
    const user = userEvent.setup()
    const view = renderPage(<AdminAppsPage />, { route: '/admin' })

    await user.click(await screen.findByRole('button', { name: 'Card view' }))
    expect(localStorage.getItem('portalAdminView')).toBe('grid')

    view.unmount()
    renderPage(<AdminAppsPage />, { route: '/admin' })
    expect(await screen.findByRole('button', { name: 'Card view' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
  })

  it('links a creator to the wizard from the "New app" action', async () => {
    renderPage(<AdminAppsPage />, { route: '/admin' })

    const link = await screen.findByRole('link', { name: 'New app' })
    expect(link).toHaveAttribute('href', '/admin/apps/new')
  })

  it('hides "New app" from an admin who may not create', async () => {
    renderPage(<AdminAppsPage />, { route: '/admin', me: adminNoCreateMe })

    // Wait for the page proper, not just the spinner.
    await screen.findByRole('link', { name: /Treatment Pathway/ })
    expect(screen.queryByRole('link', { name: 'New app' })).not.toBeInTheDocument()
    expect(screen.queryByText('New app')).not.toBeInTheDocument()
  })

  it('sends a building app to its build screen rather than its settings', async () => {
    apps.mockResolvedValue([
      ...rows,
      app({
        host: 'access-atlas.tools.stratevi.com',
        app_key: 'access-atlas',
        label: 'Access Atlas',
        status: 'building',
        live_state: 'building',
        desired_count: 0,
        running_count: 0,
      }),
    ])
    renderPage(<AdminAppsPage />, { route: '/admin' })

    const link = await screen.findByRole('link', { name: /Access Atlas/ })
    expect(link).toHaveAttribute(
      'href',
      '/admin/apps/access-atlas.tools.stratevi.com/build',
    )
    expect(screen.getByText('Building')).toBeInTheDocument()
  })
})
