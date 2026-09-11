import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'
import { adminMe, clientMe, renderPage } from '../test/render'
import { NavUser } from './NavUser'
import { Sidebar, SidebarProvider } from './sidebar'

const SIDEBAR_KEY = 'sidebar_state'

/**
 * The account block, and specifically the one thing in it that leaves the
 * SPA. Sign-out is a real navigation to a route the proxy owns, not an API
 * call — the assertions below are about the href, because that href is the
 * whole feature from this side.
 */
const account = () => screen.getByRole('button', { name: /@/ })

/**
 * The block only exists inside the rail, which owns the collapsed state.
 * Wrapped in the real `Sidebar` (not just its provider) so the tests below
 * see the same `overflow-hidden` ancestor NavUser actually renders behind in
 * AppSidebar — without it, a clipping regression wouldn't show up here.
 */
const block = (
  <SidebarProvider>
    <Sidebar>
      <NavUser />
    </Sidebar>
  </SidebarProvider>
)

async function openMenu() {
  const user = userEvent.setup()
  await user.click(account())
  return user
}

describe('the account menu', () => {
  it('offers sign out', async () => {
    renderPage(block, { me: adminMe })
    await openMenu()

    const item = within(screen.getByRole('menu')).getByRole('menuitem', {
      name: 'Sign out',
    })
    // The proxy's reserved namespace, not an SPA route and not /api/v1: it
    // answers with expired cookies and a redirect to Cognito.
    expect(item).toHaveAttribute('href', '/__proxy/logout')
  })

  it('offers it to a plain client too — signing out is not a permission', async () => {
    renderPage(block, { me: clientMe })
    await openMenu()

    expect(screen.getByRole('menuitem', { name: 'Sign out' })).toHaveAttribute(
      'href',
      '/__proxy/logout',
    )
  })

  it('asks for no confirmation — the item is the action', async () => {
    renderPage(block, { me: adminMe })
    await openMenu()

    const menu = within(screen.getByRole('menu'))
    expect(menu.queryByRole('dialog')).not.toBeInTheDocument()
    expect(menu.queryByRole('button', { name: /sign out/i })).not.toBeInTheDocument()
  })

  it('keeps sign out out of sight until the menu is open', () => {
    renderPage(block, { me: adminMe })
    expect(screen.queryByRole('menuitem', { name: 'Sign out' })).not.toBeInTheDocument()
  })

  it('renders nothing at all before /me answers', () => {
    renderPage(block, { me: null })
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.queryByRole('menuitem')).not.toBeInTheDocument()
  })
})

/**
 * Collapsed, the rail is only icon-wide and its inner wrapper carries
 * `overflow-hidden` (sidebar.tsx's `Sidebar` — it protects the collapsed
 * group label's transition spill, see the comment there). A popover
 * positioned inline with `absolute` is clipped away by that ancestor the
 * moment it steps outside the rail's own width, which is exactly what the
 * collapsed placement (`left-full`) does. These pin down the fix: the
 * collapsed popover escapes via a portal instead of being clipped.
 */
describe('the account menu, collapsed', () => {
  afterEach(() => {
    localStorage.clear()
  })

  it('escapes the rail instead of being clipped by its overflow-hidden wrapper', async () => {
    localStorage.setItem(SIDEBAR_KEY, 'collapsed')
    renderPage(block, { me: adminMe })
    await openMenu()

    const menu = screen.getByRole('menu')
    expect(menu.closest('.overflow-hidden')).toBeNull()
    expect(menu).toHaveStyle({ position: 'fixed' })
  })

  it('stays inline, with no portal, when the rail is expanded', async () => {
    localStorage.setItem(SIDEBAR_KEY, 'expanded')
    renderPage(block, { me: adminMe })
    await openMenu()

    const menu = screen.getByRole('menu')
    expect(menu.closest('.overflow-hidden')).not.toBeNull()
  })

  it('still dismisses on click-away once portaled', async () => {
    localStorage.setItem(SIDEBAR_KEY, 'collapsed')
    renderPage(block, { me: adminMe })
    const user = await openMenu()
    expect(screen.getByRole('menu')).toBeInTheDocument()

    await user.click(document.body)
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })

  it('still dismisses on Escape once portaled', async () => {
    localStorage.setItem(SIDEBAR_KEY, 'collapsed')
    renderPage(block, { me: adminMe })
    const user = await openMenu()
    expect(screen.getByRole('menu')).toBeInTheDocument()

    await user.keyboard('{Escape}')
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })
})
