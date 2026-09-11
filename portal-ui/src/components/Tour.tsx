import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useId,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type RefObject,
} from 'react'
import { useLocation } from 'react-router-dom'
import { buttonClass, Button } from './Button'
import { CloseIcon } from './icons'

/**
 * A hand-rolled walkthrough, in the spirit of `sidebar.tsx`: no driver.js or
 * floating-ui, just DOM queries and the same fixed-position-plus-clamp
 * placement `NavUser`'s collapsed popover already uses.
 *
 * The tour does not know what the rest of the app looks like — it only knows
 * a handful of DOM shapes that are already stable public contracts: the
 * rail's `<nav aria-label="Main">` landmark and the accessible names on its
 * links (the same names Layout.test.tsx already asserts by), and the
 * dashboard's card grid. Each step re-resolves its target live, on the
 * moment the tour opens, and a step whose target is not there — a link a
 * plain client cannot see, a card grid on a page that is not the dashboard —
 * is left out rather than shown pointing at nothing. The step counter is
 * built from what is left, so a client with one nav entry gets a two-step
 * tour, not four apologies.
 */

const VIEWPORT_MARGIN = 12
const POPOVER_GAP = 10
const POPOVER_WIDTH = 320

interface TourStep {
  id: string
  title: string
  body: string
  /** Finds the real element this step points at, or null if it isn't here right now. */
  select: () => HTMLElement | null
  /** An extra gate beyond "is the element there" — e.g. only on the dashboard route. */
  when?: (pathname: string) => boolean
}

/**
 * By accessible name rather than `href`: it is what the existing shell tests
 * already key off (`nav().getByRole('link', { name: 'New app' })` in
 * Layout.test.tsx), and it keeps working if a route changes under a label
 * this file does not own.
 */
function navLinkByName(name: string): HTMLElement | null {
  const nav = document.querySelector('nav[aria-label="Main"]')
  if (!nav) return null
  for (const link of Array.from(nav.querySelectorAll('a'))) {
    if (link.textContent?.trim() === name) return link
  }
  return null
}

/**
 * The dashboard's card grid (`MenuPage`). Gated to the `/` route as well as
 * to this selector matching, because `AdminAppsPage` renders a
 * classes-identical `<ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">`
 * for a different collection — the route is the only thing that tells them
 * apart, and only one of the two routes is ever mounted at a time.
 */
function dashboardGrid(): HTMLElement | null {
  return document.querySelector<HTMLElement>('main ul.grid')
}

const STEPS: TourStep[] = [
  {
    id: 'apps',
    title: 'Apps',
    body: "This takes you to your dashboard — every tool that's been shared with you, in one place.",
    select: () => navLinkByName('Apps'),
  },
  {
    id: 'dashboard-cards',
    title: 'Your tools',
    body:
      "Each card is one tool: its name, a live link, and a badge for whether it's awake, asleep, or waking up. Apps sleep when idle to save cost, so the first open after a while takes about 30–60 seconds.",
    when: (pathname) => pathname === '/',
    select: dashboardGrid,
  },
  {
    id: 'new-app',
    title: 'Create an app',
    body: 'Upload a Shiny app as a zip and it gets its own private link. It builds in about 15 minutes.',
    select: () => navLinkByName('New app'),
  },
  {
    id: 'manage-apps',
    title: 'Manage apps',
    body:
      "The admin list for everything on the platform — who can open each app, when it expires, and whether it's disabled.",
    select: () => navLinkByName('Manage apps'),
  },
  {
    id: 'costs',
    title: 'Costs',
    body: 'What each app costs to run, based on the time it was actually awake. These are estimates, not bills.',
    select: () => navLinkByName('Costs'),
  },
  {
    id: 'help',
    title: 'Help',
    body: "Guides and support if you get stuck.",
    select: () => navLinkByName('Help'),
  },
]

/**
 * A step's target counts only if it is actually on screen: connected to the
 * document, not `hidden`, not `display:none`/`visibility:hidden`, and — when
 * it has real layout at all — not sitting entirely outside the viewport. The
 * last clause is what "off-screen or inside a closed drawer" comes down to
 * in this shell: a closed mobile drawer does not render its links at all
 * (`Sidebar` returns `null`), so those already fail the first check; this
 * one is for anything a future layout might merely scroll out of view
 * instead. `hasLayout` guards it so an environment with no real layout
 * engine (jsdom in tests) does not treat every element's default zero rect
 * as "off-screen".
 */
function isUsable(el: HTMLElement | null): el is HTMLElement {
  if (!el || !el.isConnected || el.hidden) return false
  const style = window.getComputedStyle(el)
  if (style.display === 'none' || style.visibility === 'hidden') return false

  const rect = el.getBoundingClientRect()
  const hasLayout = rect.width > 0 || rect.height > 0 || rect.top !== 0 || rect.left !== 0
  if (hasLayout) {
    const offscreen =
      rect.bottom <= 0 ||
      rect.right <= 0 ||
      rect.top >= window.innerHeight ||
      rect.left >= window.innerWidth
    if (offscreen) return false
  }
  return true
}

interface Placement {
  popover: CSSProperties
  highlight: CSSProperties
}

/**
 * Where the popover and its highlight ring sit, in fixed viewport
 * coordinates — the same clamp-to-edges approach as `NavUser`'s
 * `usePopoverPlacement`. Prefers just under the target, flips above it when
 * that would run off the bottom, and always clamps inside the margins.
 */
function useStepPlacement(
  active: boolean,
  select: (() => HTMLElement | null) | undefined,
  popoverRef: RefObject<HTMLElement>,
) {
  const [placement, setPlacement] = useState<Placement | null>(null)

  useLayoutEffect(() => {
    if (!active || !select) {
      setPlacement(null)
      return
    }

    function place() {
      const target = select?.()
      if (!target) return
      const rect = target.getBoundingClientRect()
      const width = popoverRef.current?.offsetWidth ?? POPOVER_WIDTH
      const height = popoverRef.current?.offsetHeight ?? 0

      let top = rect.bottom + POPOVER_GAP
      if (top + height > window.innerHeight - VIEWPORT_MARGIN) {
        top = rect.top - POPOVER_GAP - height
      }
      top = Math.max(VIEWPORT_MARGIN, Math.min(top, window.innerHeight - height - VIEWPORT_MARGIN))

      let left = rect.left
      left = Math.max(VIEWPORT_MARGIN, Math.min(left, window.innerWidth - width - VIEWPORT_MARGIN))

      setPlacement({
        popover: { position: 'fixed', top, left, width },
        highlight: {
          position: 'fixed',
          top: rect.top - 4,
          left: rect.left - 4,
          width: rect.width + 8,
          height: rect.height + 8,
        },
      })
    }

    place()
    const raf = requestAnimationFrame(place)
    window.addEventListener('resize', place)
    window.addEventListener('scroll', place, true)
    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener('resize', place)
      window.removeEventListener('scroll', place, true)
    }
  }, [active, select, popoverRef])

  return placement
}

/** The rail control's own weight (a `sm`/`ghost` skin, not a call to action). */
const TRIGGER_CLASS = buttonClass('ghost', 'sm')

export function Tour() {
  const location = useLocation()
  const [open, setOpen] = useState(false)
  const [steps, setSteps] = useState<TourStep[]>([])
  const [stepIndex, setStepIndex] = useState(0)

  const triggerRef = useRef<HTMLButtonElement>(null)
  const dialogRef = useRef<HTMLDivElement>(null)

  const titleId = useId()
  const bodyId = useId()

  const current = steps[stepIndex]
  const placement = useStepPlacement(open, current?.select, dialogRef)

  const handleOpen = useCallback(() => {
    const pathname = location.pathname
    const resolved = STEPS.filter((step) => {
      if (step.when && !step.when(pathname)) return false
      return isUsable(step.select())
    })

    // A real element is always preferred; this is only reached if a client's
    // permissions and the current route leave nothing to point at (e.g. a
    // closed mobile drawer on a page with no card grid of its own).
    const fallback: TourStep = {
      id: 'fallback',
      title: 'Tour',
      body: "There's nothing to point at from here. Open the navigation menu, or go to your dashboard, then run the tour again.",
      select: () => triggerRef.current,
    }

    setSteps(resolved.length > 0 ? resolved : [fallback])
    setStepIndex(0)
    setOpen(true)
  }, [location.pathname])

  const handleClose = useCallback(() => {
    setOpen(false)
    triggerRef.current?.focus()
  }, [])

  const handleNext = useCallback(() => {
    setStepIndex((index) => Math.min(index + 1, steps.length - 1))
  }, [steps.length])

  const handleBack = useCallback(() => {
    setStepIndex((index) => Math.max(0, index - 1))
  }, [])

  const isLast = stepIndex >= steps.length - 1

  function handlePrimary() {
    if (isLast) handleClose()
    else handleNext()
  }

  // Focus the dialog itself once it mounts — there is no natural first field,
  // so the container takes it (tabIndex=-1), same call the APG dialog
  // pattern makes when a `initialFocusRef` is not supplied.
  useEffect(() => {
    if (open) dialogRef.current?.focus()
  }, [open, stepIndex])

  useEffect(() => {
    if (!open) return
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') handleClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [open, handleClose])

  // Tab does not escape into the page behind the scrim: cycle within the
  // dialog's own focusables instead. There is no library trap here, same
  // house rule as `sidebar.tsx` and `NavUser`'s own click-away handling.
  function trapTab(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key !== 'Tab') return
    const container = dialogRef.current
    if (!container) return
    const focusables = Array.from(
      container.querySelectorAll<HTMLElement>('button, a[href], [tabindex]:not([tabindex="-1"])'),
    ).filter((el) => !el.hasAttribute('disabled'))
    if (focusables.length === 0) {
      event.preventDefault()
      return
    }
    const first = focusables[0]
    const last = focusables[focusables.length - 1]
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }

  return (
    <>
      <button ref={triggerRef} type="button" onClick={handleOpen} className={TRIGGER_CLASS}>
        Tour
      </button>

      {open && current ? (
        <>
          <div
            aria-hidden="true"
            data-testid="tour-scrim"
            onClick={handleClose}
            className="fixed inset-0 z-[60] bg-foreground/40 backdrop-blur-[1px] transition-opacity duration-150 motion-reduce:transition-none"
          />
          {placement ? (
            <div
              aria-hidden="true"
              style={placement.highlight}
              className="pointer-events-none fixed z-[61] rounded-md ring-2 ring-azure ring-offset-2 ring-offset-background transition-[top,left,width,height] duration-150 motion-reduce:transition-none"
            />
          ) : null}
          <div
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            aria-describedby={bodyId}
            tabIndex={-1}
            onKeyDown={trapTab}
            style={
              placement?.popover ?? {
                position: 'fixed',
                top: VIEWPORT_MARGIN,
                left: VIEWPORT_MARGIN,
                width: POPOVER_WIDTH,
              }
            }
            className="z-[61] max-w-[calc(100vw-1.5rem)] rounded-lg border border-border bg-popover p-4 text-popover-foreground shadow-lg outline-none transition-opacity duration-150 motion-reduce:transition-none"
          >
            <div className="flex items-start justify-between gap-3">
              <h2 id={titleId} className="text-sm font-semibold text-foreground">
                {current.title}
              </h2>
              <button
                type="button"
                onClick={handleClose}
                aria-label="Close tour"
                className="-mr-1 -mt-1 flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
              >
                <CloseIcon className="h-3.5 w-3.5" />
              </button>
            </div>

            <p id={bodyId} className="mt-2 text-sm leading-relaxed text-muted-foreground">
              {current.body}
            </p>

            <div className="mt-4 flex items-center justify-between gap-3">
              <span className="text-xs text-muted-foreground/70">
                {stepIndex + 1} of {steps.length}
              </span>
              <div className="flex gap-2">
                {stepIndex > 0 ? (
                  <Button type="button" variant="outline" size="sm" onClick={handleBack}>
                    Back
                  </Button>
                ) : null}
                <Button type="button" variant="primary" size="sm" onClick={handlePrimary}>
                  {isLast ? 'Done' : 'Next'}
                </Button>
              </div>
            </div>
          </div>
        </>
      ) : null}
    </>
  )
}
