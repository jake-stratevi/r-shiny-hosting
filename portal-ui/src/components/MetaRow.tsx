import type { ReactNode } from 'react'

/**
 * A card's metadata line: a small outline icon and a short fact, ported from
 * assembled.work's `PreviewCard` footer (`size-3.5` glyph, `text-xs`
 * muted text, `gap-x-3 gap-y-1` between items).
 *
 * Theirs keeps every item on one line (`whitespace-nowrap`); ours may carry a
 * whole sentence — "Starts in ~30–60s when opened" — so the text is allowed
 * to wrap and the icon is pinned to the first line instead.
 */
export type MetaTone = 'muted' | 'good' | 'info' | 'warn' | 'bad'

const TONE: Record<MetaTone, string> = {
  muted: 'text-muted-foreground',
  good: 'text-emerald-700 dark:text-emerald-300',
  info: 'text-azure',
  warn: 'text-amber-800 dark:text-amber-300',
  bad: 'text-red-700 dark:text-red-300',
}

export function MetaRow({
  icon,
  children,
  tone = 'muted',
  title,
}: {
  icon: ReactNode
  children: ReactNode
  tone?: MetaTone
  title?: string
}) {
  return (
    <span className={`flex min-w-0 items-start gap-1.5 ${TONE[tone]}`} title={title}>
      <span aria-hidden="true" className="mt-px shrink-0 [&>svg]:h-3.5 [&>svg]:w-3.5">
        {icon}
      </span>
      <span className="min-w-0">{children}</span>
    </span>
  )
}

/** The block they sit in. Wraps onto one line each when the card is narrow. */
export function MetaRows({ children }: { children: ReactNode }) {
  return <div className="flex flex-wrap items-start gap-x-3 gap-y-1.5 text-xs">{children}</div>
}
