import type { LiveState } from '../api/types'

interface Style {
  label: string
  chip: string
  dot: string
  /** `starting` is the only state that moves, and only subtly. */
  pulse?: boolean
}

/**
 * One tint system, no black badges (assembled.work's StatusBadge rule):
 * emerald = good, amber = in progress or needs a look, red = stopped,
 * neutral outline = off. The dot reinforces the label; the label always
 * carries the meaning on its own.
 *
 * Each chip is stated twice — a pale tint on paper, a translucent 400 on ink
 * — because a `-50` fill on a near-black card is a white blob. The neutral
 * chips use the tokens, so they follow the theme without a variant at all.
 */
const CHIP = {
  emerald:
    'bg-emerald-100 text-emerald-800 ring-emerald-600/20 dark:bg-emerald-400/15 dark:text-emerald-300 dark:ring-emerald-400/25',
  amber:
    'bg-amber-100 text-amber-800 ring-amber-600/25 dark:bg-amber-400/15 dark:text-amber-300 dark:ring-amber-400/25',
  red: 'bg-red-100 text-red-800 ring-red-600/20 dark:bg-red-400/15 dark:text-red-300 dark:ring-red-400/25',
  neutral: 'bg-muted text-muted-foreground ring-border',
  outline: 'bg-card/90 text-muted-foreground ring-border',
} as const

const STYLES: Record<LiveState, Style> = {
  awake: {
    label: 'Awake',
    chip: CHIP.emerald,
    dot: 'bg-emerald-500',
  },
  starting: {
    label: 'Starting',
    chip: CHIP.amber,
    dot: 'bg-amber-500',
    pulse: true,
  },
  asleep: {
    label: 'Asleep',
    chip: CHIP.neutral,
    dot: 'bg-muted-foreground/60',
  },
  disabled: {
    label: 'Disabled',
    chip: CHIP.outline,
    dot: 'bg-muted-foreground/50',
  },
  expired: {
    label: 'Expired',
    chip: CHIP.red,
    dot: 'bg-red-500',
  },
  // P2a. Building is work in progress, so it borrows `starting`'s amber and
  // its pulse; a failed build is a stopped app, so it is red like `expired`.
  building: {
    label: 'Building',
    chip: CHIP.amber,
    dot: 'bg-amber-500',
    pulse: true,
  },
  build_failed: {
    label: 'Build failed',
    chip: CHIP.red,
    dot: 'bg-red-500',
  },
}

const UNKNOWN: Style = {
  label: 'Unknown',
  chip: CHIP.neutral,
  dot: 'bg-muted-foreground/60',
}

/**
 * The one place a live_state turns into pixels. `building` and `build_failed`
 * arrived with P2a; anything still unrecognised falls back to a neutral chip
 * showing the raw value rather than crashing.
 */
export function StatusBadge({
  state,
  className = '',
  title,
}: {
  state: LiveState | string
  className?: string
  /** Native tooltip — used to hang the wake-time hint off a chip. */
  title?: string
}) {
  const known = STYLES[state as LiveState]
  const style = known ?? { ...UNKNOWN, label: String(state) }

  return (
    <span
      title={title}
      className={`inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset ${style.chip} ${className}`}
    >
      <span
        aria-hidden="true"
        className={`h-1.5 w-1.5 rounded-full ${style.dot} ${
          style.pulse ? 'motion-safe:animate-pulse' : ''
        }`}
      />
      {style.label}
    </span>
  )
}
