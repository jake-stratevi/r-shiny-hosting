/**
 * The icon set, hand-drawn.
 *
 * assembled.work pulls these from lucide; a component library for fifteen
 * glyphs would be most of the bundle budget, so these are traced by hand on
 * the same 24-grid with the same 1.75 stroke, and they inherit `currentColor`
 * so a chip or a button tints them for free.
 */
import type { ReactNode, SVGProps } from 'react'

type Props = Omit<SVGProps<SVGSVGElement>, 'children'> & { className?: string }

function svg(path: ReactNode) {
  return function Glyph({ className = 'h-4 w-4', ...rest }: Props) {
    return (
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.75}
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
        focusable="false"
        className={className}
        {...rest}
      >
        {path}
      </svg>
    )
  }
}

export const SearchIcon = svg(
  <>
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3.6-3.6" />
  </>,
)

export const GridIcon = svg(
  <>
    <rect x="3.5" y="3.5" width="7" height="7" rx="1.5" />
    <rect x="13.5" y="3.5" width="7" height="7" rx="1.5" />
    <rect x="3.5" y="13.5" width="7" height="7" rx="1.5" />
    <rect x="13.5" y="13.5" width="7" height="7" rx="1.5" />
  </>,
)

export const ListIcon = svg(
  <>
    <path d="M8 6h12M8 12h12M8 18h12" />
    <path d="M4 6h.01M4 12h.01M4 18h.01" />
  </>,
)

export const ExternalIcon = svg(
  <>
    <path d="M14 4h6v6" />
    <path d="M20 4 11 13" />
    <path d="M18 14v5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 19V8a1.5 1.5 0 0 1 1.5-1.5H10" />
  </>,
)

export const CopyIcon = svg(
  <>
    <rect x="9" y="9" width="11" height="11" rx="2" />
    <path d="M5 15a2 2 0 0 1-1-1.7V6a2 2 0 0 1 2-2h7.3A2 2 0 0 1 15 5" />
  </>,
)

export const ClockIcon = svg(
  <>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 7.5V12l3 1.8" />
  </>,
)

export const CalendarIcon = svg(
  <>
    <rect x="3.5" y="5" width="17" height="15.5" rx="2" />
    <path d="M3.5 10h17M8 3.5V6.5M16 3.5V6.5" />
  </>,
)

/**
 * Expiry: a calendar with the clock face lucide's `CalendarClock` puts in its
 * bottom-right corner. The plain `CalendarIcon` means "a date"; this one
 * means "a date this thing runs out on", which is a different fact.
 */
export const CalendarClockIcon = svg(
  <>
    <path d="M20.5 11.5V7a2 2 0 0 0-2-2h-13a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2H12" />
    <path d="M3.5 10h17M8 3.5V6.5M16 3.5V6.5" />
    <circle cx="17.5" cy="17.5" r="4" />
    <path d="M17.5 15.8v1.8l1.3.8" />
  </>,
)

export const UsersIcon = svg(
  <>
    <circle cx="9.5" cy="8.5" r="3.5" />
    <path d="M3.5 19.5a6 6 0 0 1 12 0" />
    <path d="M16.5 5.6a3.5 3.5 0 0 1 0 5.9M17.5 14.2a6 6 0 0 1 3 5.3" />
  </>,
)

export const SlidersIcon = svg(
  <>
    <path d="M5 20v-6M5 10V4M12 20v-9M12 7V4M19 20v-3M19 13V4" />
    <path d="M2.5 14h5M9.5 7h5M16.5 17h5" />
  </>,
)

export const HistoryIcon = svg(
  <>
    <path d="M3.7 9.5A8.5 8.5 0 1 1 3.5 13" />
    <path d="M3.5 4.5v5h5" />
    <path d="M12 8v4.3l3 1.7" />
  </>,
)

export const PlusIcon = svg(<path d="M12 5v14M5 12h14" />)

export const ArrowLeftIcon = svg(
  <>
    <path d="M19 12H5" />
    <path d="m11 6-6 6 6 6" />
  </>,
)

export const ArrowRightIcon = svg(
  <>
    <path d="M5 12h14" />
    <path d="m13 6 6 6-6 6" />
  </>,
)

export const CheckIcon = svg(<path d="m4.5 12.5 5 5 10-11" />)

export const InfoIcon = svg(
  <>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 11v5.5M12 7.8h.01" />
  </>,
)

export const PowerIcon = svg(
  <>
    <path d="M12 3.5v8" />
    <path d="M17.3 6.7a7.5 7.5 0 1 1-10.6 0" />
  </>,
)

export const BoltIcon = svg(<path d="M13 2.5 4.5 13.5H11l-.5 8L19 10.5h-6.5z" />)

/** A zipped bundle: a box with the archive's zipper down its face. */
export const ArchiveIcon = svg(
  <>
    <rect x="3.5" y="4.5" width="17" height="15" rx="2" />
    <path d="M3.5 9.5h17" />
    <path d="M12 12v1.5M12 16v1.5" />
  </>,
)

export const PackageIcon = svg(
  <>
    <path d="m12 3 8 4.5v9L12 21l-8-4.5v-9z" />
    <path d="M4 7.5 12 12l8-4.5M12 12v9" />
  </>,
)

export const RocketIcon = svg(
  <>
    <path d="M13.5 4.5c3.5-1.5 6-1 6-1s.5 2.5-1 6c-1.3 3-4 5.2-6.5 6.2L8.3 11.5C9.3 9 11 6 13.5 4.5Z" />
    <circle cx="15" cy="9" r="1.5" />
    <path d="m8.3 11.5-3 1 2 2M12.5 15.7l1 3 2-3" />
  </>,
)

export const AlertIcon = svg(
  <>
    <path d="M10.6 4.2 2.9 17.5A1.6 1.6 0 0 0 4.3 20h15.4a1.6 1.6 0 0 0 1.4-2.5L13.4 4.2a1.6 1.6 0 0 0-2.8 0Z" />
    <path d="M12 9.5v4M12 16.8h.01" />
  </>,
)

/* ── The shell: sidebar, appearance toggle, user menu ───────────────────── */

export const SunIcon = svg(
  <>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.2 5.2l1.4 1.4M17.4 17.4l1.4 1.4M18.8 5.2l-1.4 1.4M6.6 17.4l-1.4 1.4" />
  </>,
)

export const MoonIcon = svg(
  <path d="M20 14.3A8.5 8.5 0 0 1 9.7 4a8.5 8.5 0 1 0 10.3 10.3Z" />,
)

export const MonitorIcon = svg(
  <>
    <rect x="2.5" y="4" width="19" height="12.5" rx="2" />
    <path d="M8.5 20.5h7M12 16.5v4" />
  </>,
)

/** The rail control. One glyph; the chevron flips with the state. */
export const PanelLeftIcon = svg(
  <>
    <rect x="3" y="4" width="18" height="16" rx="2" />
    <path d="M9.5 4v16" />
  </>,
)

export const MenuIcon = svg(<path d="M4 7h16M4 12h16M4 17h16" />)

export const CloseIcon = svg(<path d="M6 6l12 12M18 6 6 18" />)

export const ChevronUpDownIcon = svg(
  <>
    <path d="m8 9 4-4 4 4" />
    <path d="m16 15-4 4-4-4" />
  </>,
)

/** Administration: a shield, because "manage apps" is a permission, not a place. */
export const ShieldIcon = svg(
  <>
    <path d="M12 3.2 4.8 6v6c0 4.3 3 7.4 7.2 8.8 4.2-1.4 7.2-4.5 7.2-8.8V6z" />
    <path d="m9 12 2.2 2.2L15.2 10" />
  </>,
)

export const LogOutIcon = svg(
  <>
    <path d="M14.5 4.5H18A1.5 1.5 0 0 1 19.5 6v12a1.5 1.5 0 0 1-1.5 1.5h-3.5" />
    <path d="M10 8.5 6.5 12l3.5 3.5" />
    <path d="M6.5 12H15" />
  </>,
)

/** "New app" in the rail: a plus that reads as an action even at 16px. */
export const CirclePlusIcon = svg(
  <>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 8.5v7M8.5 12h7" />
  </>,
)

/**
 * "Costs" in the rail and on its page. A receipt rather than a dollar sign:
 * the screen reports an itemised estimate, and a currency glyph would promise
 * a bill.
 */
export const ReceiptIcon = svg(
  <>
    <path d="M6 3.5h12v17l-2-1.4-2 1.4-2-1.4-2 1.4-2-1.4-2 1.4z" />
    <path d="M9.5 8.5h5M9.5 12.5h5" />
  </>,
)
