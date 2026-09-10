import type { LiveState } from '../api/types'

interface Style {
  label: string
  chip: string
  dot: string
  /** `starting` is the only state that moves, and only subtly. */
  pulse?: boolean
}

const STYLES: Record<LiveState, Style> = {
  awake: {
    label: 'Awake',
    chip: 'bg-emerald-50 text-emerald-800 ring-emerald-600/20',
    dot: 'bg-emerald-500',
  },
  starting: {
    label: 'Starting',
    chip: 'bg-amber-50 text-amber-800 ring-amber-600/25',
    dot: 'bg-amber-500',
    pulse: true,
  },
  asleep: {
    label: 'Asleep',
    chip: 'bg-slate-100 text-slate-600 ring-slate-500/20',
    dot: 'bg-slate-400',
  },
  disabled: {
    label: 'Disabled',
    chip: 'bg-slate-800 text-slate-100 ring-slate-900/30',
    dot: 'bg-slate-400',
  },
  expired: {
    label: 'Expired',
    chip: 'bg-red-50 text-red-700 ring-red-600/20',
    dot: 'bg-red-500',
  },
}

const UNKNOWN: Style = {
  label: 'Unknown',
  chip: 'bg-slate-100 text-slate-600 ring-slate-500/20',
  dot: 'bg-slate-400',
}

/**
 * The one place a live_state turns into pixels. P2 adds `building` and
 * `build_failed`; until the API sends them, anything unrecognised falls back
 * to a neutral chip showing the raw value rather than crashing.
 */
export function StatusBadge({
  state,
  className = '',
}: {
  state: LiveState | string
  className?: string
}) {
  const known = STYLES[state as LiveState]
  const style = known ?? { ...UNKNOWN, label: String(state) }

  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset ${style.chip} ${className}`}
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
