import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { adminMe, clientMe, renderPage } from '../test/render'
import { NavUser } from './NavUser'
import { SidebarProvider } from './sidebar'

/**
 * The account block, and specifically the one thing in it that leaves the
 * SPA. Sign-out is a real navigation to a route the proxy owns, not an API
 * call — the assertions below are about the href, because that href is the
 * whole feature from this side.
 */
const account = () => screen.getByRole('button', { name: /@/ })

/** The block only exists inside the rail, which owns the collapsed state. */
const block = <SidebarProvider><NavUser /></SidebarProvider>

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
