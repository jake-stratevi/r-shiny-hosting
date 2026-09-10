import type { ReactNode } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { useMe } from '../lib/meContext'
import { AppSidebar } from './AppSidebar'
import { MenuIcon, PanelLeftIcon } from './icons'
import { SidebarInset, SidebarProvider, useSidebar } from './sidebar'

/**
 * The shell, after assembled.work's `AppSidebarLayout`: a collapsible rail
 * on the left, a slim header carrying the rail control and the trail, the
 * page, and one quiet line of footer.
 *
 * The top-nav bar this replaced put the whole product on one row and had
 * nowhere to grow; the rail has room for the two permission-gated entries
 * and still collapses to 3rem when the page wants the width.
 */
export function Layout({ children }: { children: ReactNode }) {
  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset>
        <AppHeader />
        <div className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 md:px-6 md:py-8">
          {children}
        </div>
        <footer className="mt-auto px-6 py-4 text-center text-xs text-muted-foreground/70">
          Stratevi · hosted analytics tools
        </footer>
      </SidebarInset>
    </SidebarProvider>
  )
}

function AppHeader() {
  return (
    <header className="flex h-14 shrink-0 items-center gap-2 border-b border-sidebar-border/70 px-4 md:px-6">
      <SidebarTrigger />
      <Breadcrumbs />
    </header>
  )
}

/**
 * One control for both shapes of rail: a hamburger that opens the drawer on
 * a narrow viewport, a panel glyph that collapses the column on a laptop.
 */
function SidebarTrigger() {
  const { collapsed, isMobile, openMobile, toggleSidebar } = useSidebar()

  const label = isMobile
    ? openMobile
      ? 'Close navigation'
      : 'Open navigation'
    : collapsed
      ? 'Expand sidebar'
      : 'Collapse sidebar'

  return (
    <button
      type="button"
      onClick={toggleSidebar}
      aria-label={label}
      aria-expanded={isMobile ? openMobile : !collapsed}
      className="-ml-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
    >
      {isMobile ? <MenuIcon className="h-4 w-4" /> : <PanelLeftIcon className="h-4 w-4" />}
    </button>
  )
}

interface Crumb {
  label: string
  to?: string
}

/**
 * The trail, built from the route rather than threaded through every page —
 * there are five routes and they nest predictably. The "Manage apps" parent
 * is a link only for an admin; a creator who is not one would otherwise be
 * sent to a refusal card by their own breadcrumb.
 */
function useCrumbs(): Crumb[] {
  const { pathname } = useLocation()
  const me = useMe()

  const adminCrumb: Crumb = { label: 'Manage apps', to: me?.is_admin ? '/admin' : undefined }

  if (pathname === '/') return [{ label: 'Apps' }]
  if (pathname === '/admin') return [{ label: 'Manage apps' }]
  if (pathname === '/admin/apps/new') return [adminCrumb, { label: 'New app' }]

  // The layout wraps <Routes> rather than sitting inside one, so `useParams`
  // is empty here — the host comes out of the path directly.
  // ['admin', 'apps', <host>, 'build'?]
  const segments = pathname.split('/').filter(Boolean)
  if (segments[0] === 'admin' && segments[1] === 'apps' && segments[2]) {
    const host = decodeURIComponent(segments[2])
    const detail: Crumb = {
      label: host,
      to: me?.is_admin ? `/admin/apps/${segments[2]}` : undefined,
    }
    if (segments[3] === 'build') return [adminCrumb, detail, { label: 'Build' }]
    return [adminCrumb, { label: host }]
  }

  return [{ label: 'Apps' }]
}

function Breadcrumbs() {
  const crumbs = useCrumbs()

  return (
    <nav aria-label="Breadcrumb" className="min-w-0">
      <ol className="flex min-w-0 items-center gap-1.5 text-sm">
        {crumbs.map((crumb, index) => {
          const last = index === crumbs.length - 1
          return (
            <li key={`${crumb.label}-${index}`} className="flex min-w-0 items-center gap-1.5">
              {index > 0 ? (
                <span aria-hidden="true" className="text-muted-foreground/50">
                  /
                </span>
              ) : null}
              {crumb.to && !last ? (
                <Link
                  to={crumb.to}
                  className="truncate text-muted-foreground transition-colors hover:text-foreground"
                >
                  {crumb.label}
                </Link>
              ) : (
                <span
                  aria-current={last ? 'page' : undefined}
                  className={last ? 'truncate font-medium' : 'truncate text-muted-foreground'}
                >
                  {crumb.label}
                </span>
              )}
            </li>
          )
        })}
      </ol>
    </nav>
  )
}
