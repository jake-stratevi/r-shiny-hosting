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

const ADMIN_CREATOR: Me = { email: 'jake@stratevi.com', is_admin: true, can_create: true }
const ADMIN_ONLY: Me = { email: 'nick@stratevi.com', is_admin: true, can_create: false }
const CREATOR_ONLY: Me = { email: 'yi@stratevi.com', is_admin: false, can_create: true }
const P1_BACKEND: Me = { email: 'jake@stratevi.com', is_admin: true }

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

  it('fails closed against a P1 backend that does not send can_create', async () => {
    renderApp(P1_BACKEND, '/admin/apps/new')
    expect(await screen.findByText('You can’t create apps')).toBeInTheDocument()
  })

  it('does not read "new" as a hostname', async () => {
    renderApp(ADMIN_CREATOR, '/admin/apps/new')

    await screen.findByRole('heading', { name: 'New app' })
    // The detail page's loader must never have run for a host called "new".
    expect(screen.queryByText('Loading app…')).not.toBeInTheDocument()
  })
})
