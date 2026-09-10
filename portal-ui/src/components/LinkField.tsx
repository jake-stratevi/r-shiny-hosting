import { useEffect, useRef, useState, type MouseEvent } from 'react'
import { CopyIcon, CheckIcon, ExternalIcon } from './icons'

/**
 * An app's link as a field, ported from assembled.work's `LinkField`: the
 * URL gets a row to itself with Copy and Open attached, so links have one
 * shape everywhere in the portal.
 *
 * The host is the part people read and it is at the *tail* of the string, so
 * the field scrolls horizontally (`scroll-x-thin`, their utility) instead of
 * truncating or widening the column.
 */
export function LinkField({
  url,
  /** 'live' = an app that answers now (emerald), 'idle' = one that must wake. */
  kind = 'live',
  className = '',
}: {
  url: string
  kind?: 'live' | 'idle'
  className?: string
}) {
  const [copied, setCopied] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout>>()
  const host = url.replace(/^https?:\/\//, '')

  useEffect(() => () => clearTimeout(timer.current), [])

  async function copy(event: MouseEvent) {
    // The row around this is a click target too; copying is not navigating.
    event.preventDefault()
    event.stopPropagation()
    try {
      await navigator.clipboard.writeText(url)
      setCopied(true)
      timer.current = setTimeout(() => setCopied(false), 2000)
    } catch {
      // No clipboard permission. The URL is on screen; this fails quietly.
    }
  }

  return (
    <div
      className={`flex items-center gap-1.5 rounded-md border border-border bg-muted/40 py-1 pl-2.5 pr-1 ${className}`}
    >
      <span
        aria-hidden="true"
        className={`h-1.5 w-1.5 shrink-0 rounded-full ${
          kind === 'live' ? 'bg-emerald-500' : 'bg-muted-foreground/50'
        }`}
      />
      <span
        title={url}
        className="scroll-x-thin min-w-0 flex-1 whitespace-nowrap py-0.5 font-mono text-xs text-muted-foreground"
      >
        {host}
      </span>
      <button
        type="button"
        onClick={copy}
        aria-label={copied ? 'Link copied' : `Copy link to ${host}`}
        title="Copy link"
        className="rounded p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
      >
        {copied ? (
          <CheckIcon className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
        ) : (
          <CopyIcon className="h-3.5 w-3.5" />
        )}
      </button>
      <a
        href={url}
        target="_blank"
        rel="noopener"
        onClick={(event) => event.stopPropagation()}
        aria-label={`Open ${host}`}
        title="Open app"
        className="rounded p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
      >
        <ExternalIcon className="h-3.5 w-3.5" />
      </a>
    </div>
  )
}
