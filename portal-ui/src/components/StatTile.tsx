import type { ReactNode } from 'react'
import { ArrowRightIcon } from './icons'

/**
 * A compact metric that is a door, not a plaque (assembled.work's StatTile):
 * given an `onClick` the whole tile is pressable and says where it goes on
 * hover. Here the door leads to a filter on the same page rather than a
 * route, because the admin list is one screen.
 */
export function StatTile({
  label,
  value,
  icon,
  iconClass = 'bg-background text-muted-foreground',
  onClick,
  pressed,
}: {
  label: string
  value: ReactNode
  icon?: ReactNode
  iconClass?: string
  onClick?: () => void
  /** Reflects whether this tile's filter is the one currently applied. */
  pressed?: boolean
}) {
  const body = (
    <>
      {icon ? (
        <span
          aria-hidden="true"
          className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-md ${iconClass}`}
        >
          {icon}
        </span>
      ) : null}
      <span className="min-w-0 flex-1 text-left">
        <span className="block text-xs font-medium text-muted-foreground">{label}</span>
        <span className="block text-2xl font-semibold tracking-tight text-foreground">
          {value}
        </span>
      </span>
      {onClick ? (
        <ArrowRightIcon className="h-4 w-4 shrink-0 text-muted-foreground/70 opacity-0 transition-opacity group-hover:opacity-100" />
      ) : null}
    </>
  )

  const shell = `group flex items-center gap-3 rounded-lg border bg-card px-4 py-3 shadow-sm ${
    pressed ? 'border-azure/40 ring-1 ring-azure/20' : 'border-border'
  }`

  if (!onClick) return <div className={shell}>{body}</div>

  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onClick}
      className={`${shell} w-full transition-colors hover:border-azure/40 hover:bg-accent/40`}
    >
      {body}
    </button>
  )
}
