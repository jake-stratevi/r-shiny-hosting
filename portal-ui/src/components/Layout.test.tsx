import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { adminMe, adminNoCreateMe, clientMe, creatorMe, renderPage } from '../test/render'
import { Layout } from './Layout'

/**
 * The rail replaced the top-nav bar, so the permission gating that used to
 * live in the header lives here now — and it is the same rule as the routes'
 * (App.tsx): `is_admin` and `can_create` are independent, and an absent
 * field fails closed.
 */
const shell = (
  <Layout>
    <p>page body</p>
  </Layout>
)

/** The rail's own nav; the breadcrumb in the header is a second <nav>. */
const nav = () => within(screen.getByRole('navigation', { name: 'Main' }))
const trail = () => within(screen.getByRole('navigation', { name: 'Breadcrumb' }))

beforeEach(() => {
  localStorage.clear()
  document.documentElement.className = ''
})

describe('the sidebar nav, per permission', () => {
  it('gives a plain client only Apps — no admin group at all', () => {
    renderPage(shell, { me: clientMe })

    expect(nav().getByRole('link', { name: 'Apps' })).toHaveAttribute('href', '/')
    expect(nav().queryByRole('link', { name: 'Manage apps' })).not.toBeInTheDocument()
    expect(nav().queryByRole('link', { name: 'New app' })).not.toBeInTheDocument()
    expect(nav().queryByText('Administration')).not.toBeInTheDocument()
  })

  it('gives a creator who is not an admin the wizard but not the control plane', () => {
    renderPage(shell, { me: creatorMe })

    expect(nav().getByRole('link', { name: 'New app' })).toHaveAttribute(
      'href',
      '/admin/apps/new',
    )
    expect(nav().queryByRole('link', { name: 'Manage apps' })).not.toBeInTheDocument()
    expect(nav().queryByText('Administration')).not.toBeInTheDocument()
  })

  it('gives an admin who may not create the control plane but not the wizard', () => {
    renderPage(shell, { me: adminNoCreateMe })

    expect(nav().getByRole('link', { name: 'Manage apps' })).toHaveAttribute(
      'href',
      '/admin',
    )
    expect(nav().getByText('Administration')).toBeInTheDocument()
    expect(nav().queryByRole('link', { name: 'New app' })).not.toBeInTheDocument()
  })

  it('gives an admin who may also create both', () => {
    renderPage(shell, { me: adminMe })

    expect(nav().getByRole('link', { name: 'Apps' })).toBeInTheDocument()
    expect(nav().getByRole('link', { name: 'New app' })).toBeInTheDocument()
    expect(nav().getByRole('link', { name: 'Manage apps' })).toBeInTheDocument()
  })

  it('fails closed against a backend that sends neither flag', () => {
    renderPage(shell, { me: { email: 'someone@stratevi.com' } as never })

    expect(nav().queryByRole('link', { name: 'Manage apps' })).not.toBeInTheDocument()
    expect(nav().queryByRole('link', { name: 'New app' })).not.toBeInTheDocument()
  })

  it('marks exactly one row as the current page', () => {
    renderPage(shell, { me: adminMe, route: '/admin/apps/new' })

    expect(nav().getByRole('link', { name: 'New app' })).toHaveAttribute(
      'aria-current',
      'page',
    )
    // `/admin` is a prefix of `/admin/apps/new`; it must not also light up.
    expect(nav().getByRole('link', { name: 'Manage apps' })).not.toHaveAttribute(
      'aria-current',
    )
    expect(nav().getByRole('link', { name: 'Apps' })).not.toHaveAttribute('aria-current')
  })
})

describe('the shell itself', () => {
  it('opens on the icon rail when this browser has no preference', () => {
    renderPage(shell, { me: adminMe })

    // The only control offered is the one that opens it — i.e. it is closed.
    expect(screen.getByRole('button', { name: 'Expand sidebar' })).toBeInTheDocument()
    expect(localStorage.getItem('sidebar_state')).toBeNull()
  })

  it('expands the rail and remembers it', async () => {
    const user = userEvent.setup()
    const view = renderPage(shell, { me: adminMe })

    await user.click(screen.getByRole('button', { name: 'Expand sidebar' }))
    expect(localStorage.getItem('sidebar_state')).toBe('expanded')

    view.unmount()
    renderPage(shell, { me: adminMe })
    // The stored choice beats the collapsed default, in both directions.
    expect(screen.getByRole('button', { name: 'Collapse sidebar' })).toBeInTheDocument()
  })

  it('collapses the rail again and remembers that too', async () => {
    const user = userEvent.setup()
    const view = renderPage(shell, { me: adminMe })

    await user.click(screen.getByRole('button', { name: 'Expand sidebar' }))
    await user.click(screen.getByRole('button', { name: 'Collapse sidebar' }))
    expect(localStorage.getItem('sidebar_state')).toBe('collapsed')

    view.unmount()
    renderPage(shell, { me: adminMe })
    expect(screen.getByRole('button', { name: 'Expand sidebar' })).toBeInTheDocument()
  })

  it('keeps every nav row reachable by name while the rail is icons only', () => {
    renderPage(shell, { me: adminMe })

    // The label goes visually, not from the accessibility tree.
    expect(nav().getByRole('link', { name: 'Apps' })).toBeInTheDocument()
    expect(nav().getByRole('link', { name: 'New app' })).toBeInTheDocument()
    expect(nav().getByRole('link', { name: 'Manage apps' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Stratevi Tools' })).toBeInTheDocument()
  })

  it('keeps the theme toggle and the account block operable on the icon rail', () => {
    renderPage(shell, { me: adminMe })

    const theme = within(screen.getByRole('group', { name: 'Color theme' }))
    expect(theme.getByRole('button', { name: 'Light' })).toBeInTheDocument()
    expect(theme.getByRole('button', { name: 'Dark' })).toBeInTheDocument()
    expect(theme.getByRole('button', { name: 'Match system' })).toBeInTheDocument()

    // The address is the account button's accessible name when the rail is
    // narrow enough to hide it — it must not go with the label.
    expect(
      screen.getByRole('button', { name: /jake@stratevi\.com/ }),
    ).toHaveAttribute('aria-haspopup', 'menu')
  })

  it('still marks the active row when it is an icon', () => {
    renderPage(shell, { me: adminMe, route: '/admin' })

    expect(nav().getByRole('link', { name: 'Manage apps' })).toHaveAttribute(
      'aria-current',
      'page',
    )
  })

  it('shows a trail that names where you are', () => {
    renderPage(shell, { me: adminMe, route: '/admin/apps/x.tools.stratevi.com/build' })

    expect(trail().getByRole('link', { name: 'Manage apps' })).toHaveAttribute(
      'href',
      '/admin',
    )
    expect(trail().getByText('x.tools.stratevi.com')).toBeInTheDocument()
    expect(trail().getByText('Build')).toBeInTheDocument()
  })

  it('does not offer a creator a breadcrumb link into the admin list', () => {
    renderPage(shell, { me: creatorMe, route: '/admin/apps/x.tools.stratevi.com/build' })

    expect(trail().getByText('Manage apps')).toBeInTheDocument()
    expect(trail().queryByRole('link', { name: 'Manage apps' })).not.toBeInTheDocument()
  })

  it('renders the page it is given', () => {
    renderPage(shell, { me: clientMe })
    expect(screen.getByText('page body')).toBeInTheDocument()
  })
})
