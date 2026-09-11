import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Me } from './api/types'
import App from './App'

const me = vi.hoisted(() => vi.fn())
const apps = vi.hoisted(() => vi.fn())
const menu = vi.hoisted(() => vi.fn())
const build = vi.hoisted(() => vi.fn())
const validateKey = vi.hoisted(() => vi.fn())

vi.mock('./api/client', async () => {
  const actual = await vi.importActual<typeof import('./api/client')>('./api/client')
  return { ...actual, api: { ...actual.api, me, apps, menu, build, validateKey } }
})

function renderApp(identity: Me, route: string) {
  me.mockResolvedValue(identity)
  return render(
    <MemoryRouter initialEntries={[route]}>
      <App />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  for (const mock of [me, apps, menu, build, validateKey]) mock.mockReset()
  apps.mockResolvedValue([])
  menu.mockResolvedValue([])
  build.mockResolvedValue({
    state: 'building',
    phase: 'BUILD',
    started_at: Math.floor(Date.now() / 1000),
    elapsed_s: 1,
    log_url: '',
    log_tail: [],
  })
  validateKey.mockResolvedValue({ ok: false, reason: 'Choose a key.' })
})

const ADMIN_CREATOR: Me = {
  email: 'jake@stratevi.com',
  is_admin: true,
  can_create: true,
  is_staff: true,
}
const ADMIN_ONLY: Me = {
  email: 'nick@stratevi.com',
  is_admin: true,
  can_create: false,
  is_staff: true,
}
const CREATOR_ONLY: Me = {
  email: 'yi@stratevi.com',
  is_admin: false,
  can_create: true,
  is_staff: true,
}
/** A backend that sends neither optional flag: both gates must stay shut. */
const P1_BACKEND: Me = { email: 'jake@stratevi.com', is_admin: true }
/** Staff, but a backend that has not learned `can_create` yet. */
const STAFF_NO_CREATE_FIELD: Me = {
  email: 'jake@stratevi.com',
  is_admin: true,
  is_staff: true,
}
/** An external client. Nothing on the platform but their own app menu. */
const CLIENT: Me = {
  email: 'reviewer@client-example.com',
  is_admin: false,
  can_create: false,
  is_staff: false,
}

describe('route gating: creation is its own permission', () => {
  it('lets a creator open the wizard', async () => {
    renderApp(ADMIN_CREATOR, '/admin/apps/new')
    expect(await screen.findByRole('heading', { name: 'New app' })).toBeInTheDocument()
  })

  it('refuses an admin who is not a creator, without calling it an admin problem', async () => {
    renderApp(ADMIN_ONLY, '/admin/apps/new')

    expect(await screen.findByText('You can’t create apps')).toBeInTheDocument()
    expect(screen.queryByText('You don’t have admin access')).not.toBeInTheDocument()
  })

  it('lets a creator who is not an admin reach the build screen', async () => {
    renderApp(CREATOR_ONLY, `/admin/apps/x.tools.stratevi.com/build`)
    expect(await screen.findByText(/First builds take 10–20 minutes/)).toBeInTheDocument()
  })

  it('still refuses that person the admin control plane', async () => {
    renderApp(CREATOR_ONLY, '/admin')
    expect(await screen.findByText('You don’t have admin access')).toBeInTheDocument()
  })

  it('fails closed against a backend that does not send can_create', async () => {
    renderApp(STAFF_NO_CREATE_FIELD, '/admin/apps/new')
    expect(await screen.findByText('You can’t create apps')).toBeInTheDocument()
  })

  it('does not read "new" as a hostname', async () => {
    renderApp(ADMIN_CREATOR, '/admin/apps/new')

    await screen.findByRole('heading', { name: 'New app' })
    // The detail page's loader must never have run for a host called "new".
    expect(screen.queryByText('Loading app…')).not.toBeInTheDocument()
  })
})

/**
 * The staff gate. This is the one that decides whether an external client can
 * see anything at all beyond their own app menu, so it is tested route by
 * route rather than trusting that one guard is wired everywhere: widening it
 * by accident is exactly the mistake that would not be noticed.
 */
describe('route gating: the platform is staff-only', () => {
  const STAFF_ONLY_ROUTES = [
    '/help',
    '/admin',
    '/admin/costs',
    '/admin/apps/new',
    '/admin/apps/x.tools.stratevi.com',
    '/admin/apps/x.tools.stratevi.com/build',
  ]

  for (const route of STAFF_ONLY_ROUTES) {
    it(`refuses a non-staff client ${route}`, async () => {
      renderApp(CLIENT, route)

      expect(await screen.findByText('This page is for Stratevi staff')).toBeInTheDocument()
      // The staff gate is the OUTER one, so no inner refusal — and above all
      // no page — may render underneath it.
      expect(screen.queryByText('You don’t have admin access')).not.toBeInTheDocument()
      expect(screen.queryByText('You can’t create apps')).not.toBeInTheDocument()
      expect(screen.queryByRole('heading', { name: 'Help' })).not.toBeInTheDocument()
    })
  }

  it('fails closed against a backend that does not send is_staff at all', async () => {
    renderApp(P1_BACKEND, '/help')
    expect(await screen.findByText('This page is for Stratevi staff')).toBeInTheDocument()
  })

  it('still leaves a non-staff client their own app menu', async () => {
    renderApp(CLIENT, '/')

    expect(await screen.findByText('Nothing is shared with you yet')).toBeInTheDocument()
    expect(screen.queryByText('This page is for Stratevi staff')).not.toBeInTheDocument()
  })

  it('lets staff read Help', async () => {
    renderApp(ADMIN_CREATOR, '/help')
    expect(await screen.findByRole('heading', { name: 'Help', level: 1 })).toBeInTheDocument()
  })

  it('lets a creator who is not an admin read Help', async () => {
    renderApp(CREATOR_ONLY, '/help')
    expect(await screen.findByRole('heading', { name: 'Help', level: 1 })).toBeInTheDocument()
  })
})
