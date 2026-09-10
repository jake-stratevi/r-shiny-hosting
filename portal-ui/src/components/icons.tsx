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
