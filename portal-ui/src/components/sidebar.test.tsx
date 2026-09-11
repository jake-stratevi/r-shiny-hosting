import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuItem,
  SidebarProvider,
  sidebarMenuButtonClass,
  useSidebar,
} from './sidebar'

const KEY = 'sidebar_state'

/**
 * A minimal stand-in for AppSidebar's nav — one group ("New app") whose last
 * row sits directly above a second group's label — built entirely from this
 * file's own exports so the test does not have to reach into AppSidebar.tsx,
 * which this pass does not own.
 */
function TestRail() {
  const { collapsed } = useSidebar()
  return (
    <Sidebar>
      <SidebarContent>
        <SidebarGroup>
          <SidebarMenu>
            <SidebarMenuItem>
              <button type="button" className={sidebarMenuButtonClass({ collapsed })}>
                New app
              </button>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarGroup>
        <SidebarGroup>
          <SidebarGroupLabel>Administration</SidebarGroupLabel>
          <SidebarMenu>
            <SidebarMenuItem>
              <button type="button" className={sidebarMenuButtonClass({ collapsed })}>
                Manage apps
              </button>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarGroup>
      </SidebarContent>
    </Sidebar>
  )
}

function renderRail() {
  return render(
    <SidebarProvider>
      <TestRail />
    </SidebarProvider>,
  )
}

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
})

describe('the rail\'s remembered state', () => {
  it('opens expanded for a first-time visitor with no stored preference', () => {
    renderRail()
    // A collapsed rail hides its group labels from the accessibility tree.
    expect(screen.getByText('Administration')).toHaveAttribute('aria-hidden', 'false')
  })

  it('opens expanded when site data is blocked and localStorage throws', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    renderRail()
    expect(screen.getByText('Administration')).toHaveAttribute('aria-hidden', 'false')
  })

  it('still opens collapsed when that was the stored choice', () => {
    localStorage.setItem(KEY, 'collapsed')
    renderRail()
    expect(screen.getByText('Administration')).toHaveAttribute('aria-hidden', 'true')
  })

  it('still opens expanded when that was stored explicitly', () => {
    localStorage.setItem(KEY, 'expanded')
    renderRail()
    expect(screen.getByText('Administration')).toHaveAttribute('aria-hidden', 'false')
  })
})

describe('the collapsed group label', () => {
  it('is pulled out of the hit-test tree, not just made invisible', () => {
    localStorage.setItem(KEY, 'collapsed')
    renderRail()

    const label = screen.getByText('Administration')
    expect(label).toHaveAttribute('aria-hidden', 'true')
    // `opacity-0` alone still accepts clicks; `pointer-events-none` is what
    // stops the invisible label — sitting on top of "New app", the last row
    // of the group above it — from eating them.
    expect(label).toHaveClass('pointer-events-none')
    expect(label).toHaveClass('opacity-0')
  })

  it('is a normal part of the layout when expanded, not pointer-events-none', () => {
    renderRail()

    const label = screen.getByText('Administration')
    expect(label).toHaveAttribute('aria-hidden', 'false')
    expect(label).not.toHaveClass('pointer-events-none')
  })
})
