import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'

/**
 * The shell primitives, hand-rolled.
 *
 * assembled.work builds its rail from shadcn-vue's `ui/sidebar`, which is
 * ~20 files of `group-data-[collapsible=icon]:` selectors on top of reka-ui.
 * Ported literally that is most of the bundle budget for a nav with three
 * links, so the structure is theirs and the mechanism is React: `collapsed`
 * and `isMobile` come out of a context and components branch on booleans
 * instead of on data attributes.
 *
 * Geometry (--sidebar-width: 16rem, --sidebar-width-icon: 3rem) matches
 * theirs, and so does the "inset" look: the rail sits on the sidebar colour
 * and the page floats above it as a rounded card.
 */

const SIDEBAR_KEY = 'sidebar_state'
const MOBILE_QUERY = '(max-width: 767px)'
const KEYBOARD_SHORTCUT = 'b'

interface SidebarContextValue {
  /** Desktop rail: expanded to the full width, or collapsed to icons. */
  collapsed: boolean
  setCollapsed: (value: boolean) => void
  /** Below md the rail is an off-canvas drawer instead. */
  isMobile: boolean
  openMobile: boolean
  setOpenMobile: (value: boolean) => void
  toggleSidebar: () => void
}

const SidebarContext = createContext<SidebarContextValue | null>(null)

export function useSidebar(): SidebarContextValue {
  const context = useContext(SidebarContext)
  if (!context) throw new Error('useSidebar must be used inside <SidebarProvider>')
  return context
}

/**
 * Collapsed unless this browser has said otherwise. assembled.work opens on
 * the icon rail and so do we: the first thing a client sees should be their
 * tools, not a column of three links naming screens most of them cannot open.
 * An explicit choice is stored and still wins, in both directions.
 */
function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(SIDEBAR_KEY) !== 'expanded'
  } catch {
    // Site data blocked: fall back to the default rather than to "open".
    return true
  }
}

function useIsMobile(): boolean {
  const [isMobile, setIsMobile] = useState(false)

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const query = window.matchMedia(MOBILE_QUERY)
    const sync = () => setIsMobile(query.matches)
    sync()
    query.addEventListener('change', sync)
    return () => query.removeEventListener('change', sync)
  }, [])

  return isMobile
}

export function SidebarProvider({ children }: { children: ReactNode }) {
  const [collapsed, setCollapsedState] = useState(readCollapsed)
  const [openMobile, setOpenMobile] = useState(false)
  const isMobile = useIsMobile()

  const setCollapsed = useCallback((value: boolean) => {
    setCollapsedState(value)
    try {
      window.localStorage.setItem(SIDEBAR_KEY, value ? 'collapsed' : 'expanded')
    } catch {
      // Site data blocked: the rail still collapses, it just forgets.
    }
  }, [])

  const toggleSidebar = useCallback(() => {
    if (isMobile) setOpenMobile((open) => !open)
    else setCollapsed(!collapsed)
  }, [collapsed, isMobile, setCollapsed])

  // Ctrl/Cmd-B, the same shortcut theirs binds.
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === KEYBOARD_SHORTCUT && (event.metaKey || event.ctrlKey)) {
        event.preventDefault()
        toggleSidebar()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [toggleSidebar])

  const value = useMemo(
    () => ({ collapsed, setCollapsed, isMobile, openMobile, setOpenMobile, toggleSidebar }),
    [collapsed, setCollapsed, isMobile, openMobile, toggleSidebar],
  )

  return (
    <SidebarContext.Provider value={value}>
      <div className="flex min-h-svh w-full bg-sidebar">{children}</div>
    </SidebarContext.Provider>
  )
}

/**
 * The rail itself. On a laptop it is a fixed column with a same-width spacer
 * holding the page over; below md it becomes a drawer over a scrim, because
 * a 16rem column on a 375px screen is the whole screen.
 */
export function Sidebar({ children }: { children: ReactNode }) {
  const { collapsed, isMobile, openMobile, setOpenMobile } = useSidebar()

  // Close the drawer on Escape — it is a modal layer while it is open.
  useEffect(() => {
    if (!isMobile || !openMobile) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpenMobile(false)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [isMobile, openMobile, setOpenMobile])

  if (isMobile) {
    if (!openMobile) return null
    return (
      <>
        <div
          aria-hidden="true"
          onClick={() => setOpenMobile(false)}
          className="fixed inset-0 z-40 bg-foreground/40 backdrop-blur-[1px]"
        />
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Navigation"
          className="fixed inset-y-0 left-0 z-50 flex w-[18rem] animate-slide-in-left flex-col bg-sidebar text-sidebar-foreground shadow-xl"
        >
          {children}
        </div>
      </>
    )
  }

  const width = collapsed
    ? 'w-[calc(var(--sidebar-width-icon)+1rem)]'
    : 'w-[var(--sidebar-width)]'

  return (
    <div className="hidden md:block">
      {/* Spacer: holds the page over by exactly the rail's width. */}
      <div className={`relative h-svh transition-[width] duration-200 ease-linear ${width}`} />
      <div
        className={`fixed inset-y-0 left-0 z-10 flex h-svh p-2 transition-[width] duration-200 ease-linear ${width}`}
      >
        <div className="flex h-full w-full flex-col overflow-hidden text-sidebar-foreground">
          {children}
        </div>
      </div>
    </div>
  )
}

export function SidebarHeader({ children }: { children: ReactNode }) {
  return <div className="flex flex-col gap-2 p-2">{children}</div>
}

export function SidebarContent({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto overflow-x-hidden">
      {children}
    </div>
  )
}

export function SidebarFooter({ children }: { children: ReactNode }) {
  return <div className="flex flex-col gap-2 p-2">{children}</div>
}

export function SidebarGroup({ children }: { children: ReactNode }) {
  return <div className="relative flex w-full min-w-0 flex-col px-2 py-0">{children}</div>
}

/**
 * A group heading. Collapsed to icon width it is pulled out of flow rather
 * than removed, so the icons do not jump when the rail opens again — the
 * same `-mt-8 opacity-0` trick theirs uses.
 */
export function SidebarGroupLabel({ children }: { children: ReactNode }) {
  const { collapsed, isMobile } = useSidebar()
  const hidden = collapsed && !isMobile

  return (
    <div
      className={`flex h-8 shrink-0 items-center rounded-md px-2 text-xs font-medium text-sidebar-foreground/70 transition-[margin,opacity] duration-200 ease-linear ${
        hidden ? '-mt-8 opacity-0' : ''
      }`}
      aria-hidden={hidden}
    >
      {children}
    </div>
  )
}

export function SidebarMenu({ children }: { children: ReactNode }) {
  return <ul className="flex w-full min-w-0 flex-col gap-1">{children}</ul>
}

export function SidebarMenuItem({ children }: { children: ReactNode }) {
  return <li className="relative">{children}</li>
}

export type SidebarMenuButtonSize = 'default' | 'lg'

/**
 * The class every clickable row in the rail wears. Exported rather than
 * wrapped in a component so a NavLink, an <a> and a <button> can all be one
 * — the rail holds all three.
 */
export function sidebarMenuButtonClass({
  active = false,
  size = 'default',
  collapsed = false,
}: {
  active?: boolean
  size?: SidebarMenuButtonSize
  collapsed?: boolean
} = {}): string {
  const base =
    'flex w-full items-center gap-2 overflow-hidden rounded-md p-2 text-left text-sm outline-none transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 focus-visible:ring-sidebar-ring disabled:pointer-events-none disabled:opacity-50 [&>svg]:size-4 [&>svg]:shrink-0 [&>span:last-child]:truncate'
  const height = collapsed ? 'h-8 w-8 justify-center p-2' : size === 'lg' ? 'h-12' : 'h-8'
  const state = active
    ? 'bg-sidebar-accent font-medium text-sidebar-accent-foreground'
    : ''
  return `${base} ${height} ${state}`
}

/** The main pane: the page, floating over the rail's colour as a card. */
export function SidebarInset({ children }: { children: ReactNode }) {
  return (
    <main className="relative flex w-full min-w-0 flex-1 flex-col bg-background md:my-2 md:mr-2 md:rounded-xl md:border md:border-sidebar-border md:shadow-sm">
      {children}
    </main>
  )
}
