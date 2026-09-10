import { useEffect, useRef, useState } from 'react'
import { firstNameFromEmail } from '../lib/appDisplay'
import { useMe } from '../lib/meContext'
import { ChevronUpDownIcon } from './icons'
import { sidebarMenuButtonClass, SidebarMenu, SidebarMenuItem, useSidebar } from './sidebar'

/**
 * The proxy's sign-out route. Under `/__proxy/`, which is reserved on every
 * hostname the platform serves, so it can never collide with an app's own
 * routes — and it is NOT part of the `/api/v1` contract, because nothing
 * about it is JSON: it answers with `Set-Cookie` headers and a 302.
 */
const LOGOUT_PATH = '/__proxy/logout'

/**
 * The account block pinned to the bottom of the rail, after
 * assembled.work's `NavUser` / `UserInfo`.
 *
 * Theirs opens a menu of things a Laravel app can do — switch team, edit
 * profile, log out. We have two of those: what the menu discloses is what
 * the collapsed rail hides (the full address, and which of the two
 * permissions this account carries), plus sign-out.
 *
 * Sign-out is a plain `<a>`, not a fetch and not a router link, and that is
 * deliberate. Authentication is the ALB's, not the SPA's: signing out means
 * expiring the load balancer's session cookies and then handing the browser
 * to Cognito's logout endpoint, which is a chain of top-level navigations
 * and cross-origin redirects. An XHR cannot follow it, and an SPA route
 * would leave the page mounted while the session under it disappeared. So
 * the browser leaves, and comes back on a page served without a session.
 */
export function NavUser() {
  const me = useMe()
  const { collapsed, isMobile } = useSidebar()
  const [open, setOpen] = useState(false)
  const wrapper = useRef<HTMLDivElement>(null)

  const iconOnly = collapsed && !isMobile
  const email = me?.email ?? ''
  const name = firstNameFromEmail(email)
  const initial = name.charAt(0).toUpperCase()

  // Click-away and Escape close it; this is the only popover in the shell,
  // so it dismisses itself rather than pulling in a floating-ui.
  useEffect(() => {
    if (!open) return
    function onPointerDown(event: MouseEvent) {
      if (!wrapper.current?.contains(event.target as Node)) setOpen(false)
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  if (!me) return null

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <div ref={wrapper} className="relative">
          <button
            type="button"
            aria-expanded={open}
            aria-haspopup="menu"
            title={iconOnly ? email : undefined}
            onClick={() => setOpen((value) => !value)}
            className={`${sidebarMenuButtonClass({
              size: 'lg',
              collapsed: iconOnly,
              active: open,
            })} gap-2`}
          >
            <span
              aria-hidden="true"
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-sidebar-accent text-xs font-semibold text-sidebar-accent-foreground"
            >
              {initial}
            </span>
            {iconOnly ? (
              <span className="sr-only">{email}</span>
            ) : (
              <>
                <span className="grid min-w-0 flex-1 text-left leading-tight">
                  <span className="truncate text-sm font-medium text-sidebar-foreground">
                    {name}
                  </span>
                  <span className="truncate text-xs text-sidebar-foreground/60">{email}</span>
                </span>
                <ChevronUpDownIcon className="ml-auto h-4 w-4 shrink-0" />
              </>
            )}
          </button>

          {open ? (
            <div
              role="menu"
              className={`absolute z-50 min-w-56 rounded-lg border border-border bg-popover p-1 text-popover-foreground shadow-md ${
                iconOnly ? 'bottom-0 left-full ml-2' : 'bottom-full left-0 mb-2 w-full'
              }`}
            >
              <div className="px-2 py-1.5">
                <p className="truncate text-sm font-medium">{name}</p>
                <p className="truncate text-xs text-muted-foreground">{email}</p>
              </div>
              <div className="my-1 h-px bg-border" />
              <p className="px-2 py-1 text-xs text-muted-foreground">Permissions</p>
              <ul className="px-2 pb-1.5 pt-0.5">
                <PermissionRow label="Manage apps" granted={me.is_admin === true} />
                <PermissionRow label="Create apps" granted={me.can_create === true} />
              </ul>
              <div className="my-1 h-px bg-border" />
              <a
                role="menuitem"
                href={LOGOUT_PATH}
                className="flex w-full items-center rounded-md px-2 py-1.5 text-sm hover:bg-accent hover:text-accent-foreground"
              >
                Sign out
              </a>
            </div>
          ) : null}
        </div>
      </SidebarMenuItem>
    </SidebarMenu>
  )
}

function PermissionRow({ label, granted }: { label: string; granted: boolean }) {
  return (
    <li className="flex items-center justify-between gap-3 py-0.5 text-sm">
      <span>{label}</span>
      <span
        className={
          granted
            ? 'text-xs font-medium text-emerald-700 dark:text-emerald-400'
            : 'text-xs text-muted-foreground'
        }
      >
        {granted ? 'Yes' : 'No'}
      </span>
    </li>
  )
}
