import type { ReactNode } from 'react'
import { ApiError } from '../api/client'
import { ButtonLink } from './Button'

/**
 * Shared page header: one place for the title/subtitle scale and the action
 * row, so every page opens with the same typographic voice.
 * (assembled.work PageHeader.)
 */
export function PageHeader({
  title,
  description,
  meta,
  actions,
}: {
  title: ReactNode
  description?: ReactNode
  /** A quieter third line — who you are signed in as, refresh cadence, etc. */
  meta?: ReactNode
  actions?: ReactNode
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">{title}</h1>
        {description ? <p className="mt-1 text-sm text-muted-foreground">{description}</p> : null}
        {meta ? <p className="mt-1 text-xs text-muted-foreground/70">{meta}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </div>
  )
}

export function Panel({
  children,
  className = '',
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <div className={`rounded-lg border border-border bg-card shadow-sm ${className}`}>
      {children}
    </div>
  )
}

/**
 * A panel with the quiet header strip assembled.work puts on every block on
 * the Show page ("App links", "Version history", "At a glance").
 */
export function SectionCard({
  title,
  description,
  actions,
  children,
  bodyClassName = '',
}: {
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
  children: ReactNode
  bodyClassName?: string
}) {
  return (
    <Panel className="overflow-hidden">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border/60 px-5 py-3.5">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-foreground">{title}</h2>
          {description ? (
            <p className="mt-1 max-w-2xl text-xs leading-relaxed text-muted-foreground/70">{description}</p>
          ) : null}
        </div>
        {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
      </div>
      <div className={bodyClassName}>{children}</div>
    </Panel>
  )
}

export function Loading({ label = 'Loading' }: { label?: string }) {
  return (
    <div className="flex items-center gap-3 py-16 text-sm text-muted-foreground/70" role="status">
      <span className="h-3 w-3 animate-spin rounded-full border-2 border-border border-t-accent" />
      {label}…
    </div>
  )
}

/** The empty state from assembled.work: a calm graphic, a line, a way out. */
export function EmptyState({
  title,
  children,
  action,
}: {
  title: string
  children?: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="rounded-lg border border-dashed border-border bg-card/60 px-8 py-14 text-center">
      <svg
        className="mx-auto mb-5 h-20 w-20 text-azure/25"
        viewBox="0 0 96 96"
        fill="none"
        aria-hidden="true"
      >
        <rect x="14" y="22" width="68" height="52" rx="6" stroke="currentColor" strokeWidth="3" />
        <path d="M14 34h68" stroke="currentColor" strokeWidth="3" />
        <circle cx="22" cy="28" r="2" fill="currentColor" />
        <circle cx="30" cy="28" r="2" fill="currentColor" />
        <path
          d="M32 60V48M44 60V42M56 60V52M68 60V44"
          stroke="currentColor"
          strokeWidth="3"
          strokeLinecap="round"
        />
      </svg>
      <p className="text-base font-medium text-foreground">{title}</p>
      {children ? (
        <div className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-muted-foreground">{children}</div>
      ) : null}
      {action ? <div className="mt-6 flex justify-center">{action}</div> : null}
    </div>
  )
}

/** 403 from anything under /api/v1/apps: not an admin. */
export function NoAdminAccess() {
  return (
    <div className="mx-auto max-w-lg rounded-lg border border-border bg-card px-10 py-12 text-center shadow-sm">
      <h2 className="text-base font-semibold text-foreground">You don’t have admin access</h2>
      <p className="mx-auto mt-3 max-w-sm text-sm leading-relaxed text-muted-foreground">
        This area is limited to platform administrators. Your sign-in worked
        fine — the account just isn’t on the admin list.
      </p>
      <div className="mt-6 flex justify-center">
        <ButtonLink href="/">Back to your tools</ButtonLink>
      </div>
    </div>
  )
}

/**
 * 403 from anything under the P2a creation routes. A separate card from the
 * admin one on purpose: portal-p2a.md makes creation its own permission, so
 * "you're not an admin" would be the wrong — and possibly false — reason.
 */
export function NoCreateAccess() {
  return (
    <div className="mx-auto max-w-lg rounded-lg border border-border bg-card px-10 py-12 text-center shadow-sm">
      <h2 className="text-base font-semibold text-foreground">You can’t create apps</h2>
      <p className="mx-auto mt-3 max-w-sm text-sm leading-relaxed text-muted-foreground">
        Creating an app is its own permission, separate from admin — a new app
        gets its own hostname, container and credentials, so the list of people
        who may make one is kept short and lives in Terraform.
      </p>
      <div className="mt-6 flex justify-center">
        <ButtonLink href="/">Back to your tools</ButtonLink>
      </div>
    </div>
  )
}

/** Any other failure. Renders the API's own `{error}` text when there is one. */
export function ErrorState({
  error,
  onRetry,
}: {
  error: ApiError
  onRetry?: () => void
}) {
  if (error.isForbidden) return <NoAdminAccess />

  const title = error.isNetwork
    ? 'Can’t reach the portal service'
    : error.isNotFound
      ? 'Not found'
      : error.isUnauthorized
        ? 'Session expired'
        : 'Something went wrong'

  return (
    <div className="rounded-lg border border-red-200 bg-red-50/70 px-6 py-5 dark:border-red-400/25 dark:bg-red-400/10">
      <p className="text-sm font-semibold text-red-900 dark:text-red-200">{title}</p>
      <p className="mt-1.5 text-sm leading-relaxed text-red-800 dark:text-red-300">
        {error.message}
      </p>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="mt-4 rounded-md border border-red-300 bg-card px-3 py-1.5 text-sm font-medium text-red-800 transition-colors hover:bg-red-100 dark:border-red-400/30 dark:text-red-300 dark:hover:bg-red-400/15"
        >
          Try again
        </button>
      ) : null}
    </div>
  )
}

/**
 * The state-driven notice bar from assembled.work's Show page: one card, tone
 * carried by colour, an optional action. Only rendered when something is
 * actually the matter.
 */
export function Notice({
  tone,
  title,
  children,
  action,
}: {
  tone: 'info' | 'warning' | 'danger'
  title: ReactNode
  children?: ReactNode
  action?: ReactNode
}) {
  const skin =
    tone === 'danger'
      ? 'border-red-200 bg-red-50/70 text-red-900 dark:border-red-400/25 dark:bg-red-400/10 dark:text-red-200'
      : tone === 'warning'
        ? 'border-amber-200 bg-amber-50/80 text-amber-900 dark:border-amber-400/25 dark:bg-amber-400/10 dark:text-amber-200'
        : 'border-azure/30 bg-azure/5 text-foreground'

  return (
    <div className={`rounded-lg border px-5 py-4 ${skin}`}>
      <p className="text-sm font-semibold">{title}</p>
      {children ? <div className="mt-1 text-sm leading-relaxed opacity-90">{children}</div> : null}
      {action ? <div className="mt-3">{action}</div> : null}
    </div>
  )
}

/** Small inline error, for form submits. */
export function InlineError({ children }: { children: ReactNode }) {
  return (
    <p role="alert" className="text-sm text-red-700">
      {children}
    </p>
  )
}
