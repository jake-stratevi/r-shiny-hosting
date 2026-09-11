import type { ReactNode } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { useMe } from '../lib/meContext'
import { AppearanceToggle } from './AppearanceToggle'
import { CirclePlusIcon, GridIcon, ReceiptIcon, ShieldIcon } from './icons'
import { Logo, LogoMark } from './Logo'
import { NavUser } from './NavUser'
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuItem,
  sidebarMenuButtonClass,
  useSidebar,
} from './sidebar'

interface NavItem {
  title: string
  to: string
  /** Only match this exact path (for `/`, which prefixes everything). */
  end?: boolean
  icon: ReactNode
}

/**
 * The rail, after assembled.work's `AppSidebar`: lockup at the top that
 * collapses to the bare mark, grouped nav in the middle, appearance toggle
 * and account block pinned to the footer.
 *
 * The nav is only what this product has. Theirs lists Dashboard, My Apps,
 * Shared With Me, Create App and Help; we have one menu, one admin list and
 * one wizard, so that is what the rail says. A link to a screen we have not
 * built is worse than a short menu.
 */
export function AppSidebar() {
  const me = useMe()
  const { collapsed, isMobile } = useSidebar()
  const iconOnly = collapsed && !isMobile

  const appItems: NavItem[] = [
    { title: 'Apps', to: '/', end: true, icon: <GridIcon className="h-4 w-4" /> },
  ]
  // Creation is its own permission — admin does not imply it, and it does
  // not imply admin. Both gates are independent, and both fail closed.
  if (me?.can_create) {
    appItems.push({
      title: 'New app',
      to: '/admin/apps/new',
      icon: <CirclePlusIcon className="h-4 w-4" />,
    })
  }

  const adminItems: NavItem[] = me?.is_admin
    ? [
        { title: 'Manage apps', to: '/admin', end: true, icon: <ShieldIcon className="h-4 w-4" /> },
        {
          title: 'Costs',
          to: '/admin/costs',
          end: true,
          icon: <ReceiptIcon className="h-4 w-4" />,
        },
      ]
    : []

  return (
    <Sidebar>
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <HomeLink />
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>

      <SidebarContent>
        {/* A landmark, so "the nav" is addressable — the breadcrumb in the
            header is a second <nav> and the two must not be confusable. */}
        <nav aria-label="Main" className="flex flex-col gap-2">
          <NavGroup label="Apps" items={appItems} />
          {adminItems.length > 0 ? (
            <NavGroup label="Administration" items={adminItems} />
          ) : null}
        </nav>
      </SidebarContent>

      <SidebarFooter>
        <AppearanceToggle
          orientation={iconOnly ? 'vertical' : 'horizontal'}
          className="mb-1"
        />
        <NavUser />
      </SidebarFooter>
    </Sidebar>
  )
}

/** Full lockup when the rail is open; the bare mark when it is icon-width. */
function HomeLink() {
  const { collapsed, isMobile, setOpenMobile } = useSidebar()
  const iconOnly = collapsed && !isMobile

  return (
    <NavLink
      to="/"
      onClick={() => setOpenMobile(false)}
      aria-label="Stratevi Tools"
      className={sidebarMenuButtonClass({ size: 'lg', collapsed: iconOnly })}
    >
      {iconOnly ? <LogoMark className="size-5 shrink-0" /> : <Logo />}
    </NavLink>
  )
}

function NavGroup({ label, items }: { label: string; items: NavItem[] }) {
  const { collapsed, isMobile, setOpenMobile } = useSidebar()
  const { pathname } = useLocation()
  const iconOnly = collapsed && !isMobile

  if (items.length === 0) return null

  return (
    <SidebarGroup>
      <SidebarGroupLabel>{label}</SidebarGroupLabel>
      <SidebarMenu>
        {items.map((item) => {
          // NavLink's own `isActive` treats `/admin` as active on
          // `/admin/apps/new`, which would light two rows at once; `end`
          // plus an explicit prefix test keeps exactly one lit.
          const active = item.end
            ? pathname === item.to
            : pathname === item.to || pathname.startsWith(`${item.to}/`)

          return (
            <SidebarMenuItem key={item.to}>
              <NavLink
                to={item.to}
                end={item.end}
                title={iconOnly ? item.title : undefined}
                aria-current={active ? 'page' : undefined}
                onClick={() => setOpenMobile(false)}
                className={sidebarMenuButtonClass({ active, collapsed: iconOnly })}
              >
                {item.icon}
                <span className={iconOnly ? 'sr-only' : undefined}>{item.title}</span>
              </NavLink>
            </SidebarMenuItem>
          )
        })}
      </SidebarMenu>
    </SidebarGroup>
  )
}
