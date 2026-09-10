/**
 * Our mark, not theirs. assembled.work's design language is what this pass
 * adopts; its wordmark and glyph are its own and stay there.
 *
 * Three ascending bars in a soft square: hosted analytics tools, abstract
 * enough to survive being 20px wide in a collapsed rail.
 */
export function LogoMark({ className = 'size-6' }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" focusable="false">
      <rect width="24" height="24" rx="6" className="fill-primary" />
      <g className="fill-primary-foreground">
        <rect x="6" y="13" width="3.2" height="5" rx="1.6" />
        <rect x="10.4" y="9.5" width="3.2" height="8.5" rx="1.6" />
        <rect x="14.8" y="6" width="3.2" height="12" rx="1.6" />
      </g>
    </svg>
  )
}

/** The full lockup: mark plus wordmark, for the expanded rail. */
export function Logo() {
  return (
    <>
      <LogoMark className="size-7 shrink-0" />
      <span className="grid min-w-0 flex-1 text-left leading-tight">
        <span className="truncate text-sm font-semibold tracking-tight text-sidebar-foreground">
          Stratevi
        </span>
        <span className="truncate text-xs text-sidebar-foreground/60">Tools</span>
      </span>
    </>
  )
}
