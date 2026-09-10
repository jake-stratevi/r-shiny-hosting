import type { ReactNode } from 'react'
import { ApiError } from '../api/client'

export function PageHeading({
  title,
  subtitle,
  actions,
}: {
  title: string
  subtitle?: ReactNode
  actions?: ReactNode
}) {
  return (
    <div className="mb-8 flex flex-wrap items-start justify-between gap-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">{title}</h1>
        {subtitle ? <p className="mt-1.5 text-sm text-muted">{subtitle}</p> : null}
      </div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
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
    <div
      className={`rounded-card border border-line bg-surface shadow-card ${className}`}
    >
      {children}
    </div>
  )
}

export function Loading({ label = 'Loading' }: { label?: string }) {
  return (
    <div className="flex items-center gap-3 py-16 text-sm text-faint" role="status">
      <span className="h-3 w-3 animate-spin rounded-full border-2 border-line border-t-accent" />
      {label}…
    </div>
  )
}

export function EmptyState({
  title,
  children,
}: {
  title: string
  children?: ReactNode
}) {
  return (
    <div className="rounded-card border border-dashed border-line bg-surface/60 px-8 py-14 text-center">
      <p className="text-sm font-medium text-ink">{title}</p>
      {children ? (
        <div className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-muted">
          {children}
        </div>
      ) : null}
    </div>
  )
}

/** 403 from anything under /api/v1/apps: not an admin. */
export function NoAdminAccess() {
  return (
    <div className="mx-auto max-w-lg rounded-card border border-line bg-surface px-10 py-12 text-center shadow-card">
      <h2 className="text-base font-semibold text-ink">You don’t have admin access</h2>
      <p className="mx-auto mt-3 max-w-sm text-sm leading-relaxed text-muted">
        This area is limited to platform administrators. Your sign-in worked
        fine — the account just isn’t on the admin list.
      </p>
      <a
        href="/"
        className="mt-6 inline-flex items-center rounded-md bg-accent px-3.5 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-hover"
      >
        Back to your tools
      </a>
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
    <div className="rounded-card border border-red-200 bg-red-50/70 px-6 py-5">
      <p className="text-sm font-semibold text-red-900">{title}</p>
      <p className="mt-1.5 text-sm leading-relaxed text-red-800">{error.message}</p>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="mt-4 rounded-md border border-red-300 bg-white px-3 py-1.5 text-sm font-medium text-red-800 transition-colors hover:bg-red-100"
        >
          Try again
        </button>
      ) : null}
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
