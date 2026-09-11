import { screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import type { Me } from '../api/types'
import { adminMe, adminNoCreateMe, clientMe, creatorMe, renderPage } from '../test/render'
import { AppSidebar } from './AppSidebar'
import { SidebarProvider } from './sidebar'

/**
 * The rail's permission gating, asserted EXHAUSTIVELY rather than by absence.
 *
 * Layout.test.tsx checks that specific rows are missing for specific
 * identities; the trouble with `queryBy(...).not.toBeInTheDocument()` is that
 * it says nothing about a row nobody thought to name. Somebody adding a
 * seventh screen and forgetting the staff check would pass every one of those
 * assertions. So the tests below read every row the rail renders and compare
 * the whole list.
 */
const rail = (
  <SidebarProvider>
    <AppSidebar />
  </SidebarProvider>
)

/** The rail's own nav — the breadcrumb in the header is a second <nav>. */
const nav = () => within(screen.getByRole('navigation', { name: 'Main' }))

/** Every nav row, in the order the rail renders them. */
function rows(): string[] {
  return nav()
    .getAllByRole('link')
    .map((link) => link.textContent?.trim() ?? '')
}

beforeEach(() => {
  localStorage.clear()
})

describe('the rail offers exactly the screens the routes would allow', () => {
  it('gives staff who are admins and creators all five', () => {
    renderPage(rail, { me: adminMe })
    expect(rows()).toEqual(['Apps', 'New app', 'Manage apps', 'Costs', 'Help'])
  })

  it('gives staff who may create but not administer the wizard and Help', () => {
    renderPage(rail, { me: creatorMe })
    expect(rows()).toEqual(['Apps', 'New app', 'Help'])
  })

  it('gives staff who may administer but not create the control plane and Help', () => {
    renderPage(rail, { me: adminNoCreateMe })
    expect(rows()).toEqual(['Apps', 'Manage apps', 'Costs', 'Help'])
  })

  it('gives a non-staff client exactly one row: Apps', () => {
    renderPage(rail, { me: clientMe })

    expect(rows()).toEqual(['Apps'])
    expect(nav().getByRole('link', { name: 'Apps' })).toHaveAttribute('href', '/')
    // No group heading either — an empty "Support" label would advertise a
    // screen this person cannot open.
    expect(nav().queryByText('Administration')).not.toBeInTheDocument()
    expect(nav().queryByText('Support')).not.toBeInTheDocument()
  })

  it('gives a non-staff admin-and-creator one row too — staff is the outer gate', () => {
    // Not a shape the backend should produce; asserted because the rail must
    // not be the thing that decides it cannot happen.
    const odd: Me = {
      email: 'reviewer@client-example.com',
      is_admin: true,
      can_create: true,
      is_staff: false,
    }
    renderPage(rail, { me: odd })
    expect(rows()).toEqual(['Apps'])
  })

  it('fails closed against a backend that sends no flags at all', () => {
    renderPage(rail, { me: { email: 'someone@stratevi.com' } as Me })
    expect(rows()).toEqual(['Apps'])
  })

  it('fails closed while /api/v1/me is still unknown', () => {
    renderPage(rail, { me: null })
    expect(rows()).toEqual(['Apps'])
  })

  it('lights Help, and only Help, on /help', () => {
    renderPage(rail, { me: adminMe, route: '/help' })

    expect(nav().getByRole('link', { name: 'Help' })).toHaveAttribute('aria-current', 'page')
    expect(nav().getByRole('link', { name: 'Apps' })).not.toHaveAttribute('aria-current')
  })
})
