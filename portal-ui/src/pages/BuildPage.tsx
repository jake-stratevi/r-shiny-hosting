import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { BuildStatus } from '../api/types'
import { ButtonLink, ButtonRoute } from '../components/Button'
import { StatusBadge } from '../components/StatusBadge'
import { ArrowLeftIcon, CheckIcon, ExternalIcon } from '../components/icons'
import { ErrorState, Loading, Notice, PageHeader, SectionCard } from '../components/states'
import { useResource } from '../hooks/useResource'
import { useVisiblePolling } from '../hooks/useVisiblePolling'
import { useMe } from '../lib/meContext'

/** Faster than the 15 s list poll: someone is watching this one on purpose. */
const BUILD_POLL_MS = 5_000

/**
 * The build screen, reached on the 202 from `POST /apps`.
 *
 * portal-p2a.md: "streams status honestly: which phase, elapsed time, 'first
 * builds take 10–20 minutes', and the log tail on failure. Do not fake a
 * progress bar." So there is no bar and no percentage anywhere on this page —
 * CodeBuild reports a phase name, not a fraction, and inventing one out of
 * phase ordinals would be a lie that gets slower as it goes.
 */
export function BuildPage() {
  const { host = '' } = useParams<{ host: string }>()
  const navigate = useNavigate()
  const me = useMe()

  const { data, error, loading, reload } = useResource(`build:${host}`, (signal) =>
    api.build(host, signal),
  )

  const building = data?.state === 'building' || data === null
  useVisiblePolling(() => reload({ quiet: true }), BUILD_POLL_MS, building)

  // The app detail page is admin-only; a creator who is not an admin would
  // land on a refusal card, so they get the success state here instead.
  useEffect(() => {
    if (data?.state === 'succeeded' && me?.is_admin) {
      navigate(`/admin/apps/${encodeURIComponent(host)}`, { replace: true })
    }
  }, [data?.state, host, me?.is_admin, navigate])

  if (loading) return <Loading label="Loading build status" />
  if (error) return <ErrorState error={error} onRetry={() => reload()} />
  if (!data) return null

  return (
    <div className="mx-auto w-full max-w-3xl">
      <nav className="mb-4">
        <Link
          to="/admin"
          className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-accent"
        >
          <ArrowLeftIcon className="h-4 w-4" />
          All apps
        </Link>
      </nav>

      <PageHeader
        title={host}
        description={
          data.state === 'building'
            ? 'Building the container image. You can close this tab — the build carries on without it.'
            : data.state === 'succeeded'
              ? 'The image is built and the app is registered.'
              : 'The build failed. Nothing was torn down, so the logs are still there.'
        }
        actions={<StatusBadge state={buildBadgeState(data.state)} />}
      />

      {data.state === 'building' ? <BuildingState status={data} /> : null}
      {data.state === 'failed' ? <FailedState host={host} status={data} /> : null}
      {data.state === 'succeeded' ? <SucceededState host={host} /> : null}

      <div className="mt-5">
        <LogTail status={data} />
      </div>
    </div>
  )
}

const buildBadgeState = (state: BuildStatus['state']): string =>
  state === 'building' ? 'building' : state === 'failed' ? 'build_failed' : 'awake'

function BuildingState({ status }: { status: BuildStatus }) {
  const elapsed = useElapsed(status)

  return (
    <div className="space-y-5">
      <div className="rounded-card border border-amber-200 bg-amber-50/70 px-5 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <span className="h-3 w-3 shrink-0 animate-spin rounded-full border-2 border-amber-300 border-t-amber-700" />
            <div>
              <p className="text-sm font-semibold text-amber-900">
                Phase: <span className="font-mono">{status.phase || 'starting'}</span>
              </p>
              <p className="mt-0.5 text-sm text-amber-900/90">
                First builds take 10–20 minutes, because every R package is compiled
                from source.
              </p>
            </div>
          </div>
          <div className="text-right">
            <p className="text-[11px] uppercase tracking-wide text-amber-900/70">
              Elapsed
            </p>
            <p className="font-mono text-lg font-semibold tabular-nums text-amber-900">
              {formatElapsed(elapsed)}
            </p>
          </div>
        </div>
      </div>

      <p className="text-xs leading-relaxed text-faint">
        There is deliberately no progress bar: CodeBuild reports which phase it is
        in, not how far through it is, and a bar drawn from phase numbers would
        spend most of its life wrong. The phase name and the log tail below are the
        real signal. This page re-checks every {BUILD_POLL_MS / 1000} seconds while
        the tab is visible.
      </p>
    </div>
  )
}

function FailedState({ host, status }: { host: string; status: BuildStatus }) {
  return (
    <Notice
      tone="danger"
      title="The build failed"
      action={
        <div className="flex flex-wrap gap-2">
          <ButtonRoute to="/admin/apps/new" variant="outline">
            Start over with a fixed bundle
          </ButtonRoute>
          {status.log_url ? (
            <ButtonLink
              href={status.log_url}
              variant="ghost"
              target="_blank"
              rel="noopener"
            >
              <ExternalIcon className="h-4 w-4" />
              Full log in the console
            </ButtonLink>
          ) : null}
        </div>
      }
    >
      <p>
        It stopped in <span className="font-mono">{status.phase || 'an early phase'}</span>{' '}
        after {formatElapsed(status.elapsed_s)}. Almost always this is a package
        that will not install — read the last lines below for the one that failed,
        then fix the bundle or drop the package from the list.
      </p>
      <p className="mt-2">
        The row for <span className="font-mono">{host}</span> and its half-built
        resources were kept on purpose, so nothing has to be guessed at. Deleting
        them is a P2b job.
      </p>
    </Notice>
  )
}

function SucceededState({ host }: { host: string }) {
  return (
    <Notice
      tone="info"
      title="It's live"
      action={
        <div className="flex flex-wrap gap-2">
          <ButtonLink href={`https://${host}`} target="_blank" rel="noopener">
            <ExternalIcon className="h-4 w-4" />
            Open the app
          </ButtonLink>
          <ButtonRoute to="/" variant="outline">
            Back to your tools
          </ButtonRoute>
        </div>
      }
    >
      <p className="flex items-center gap-1.5">
        <CheckIcon className="h-4 w-4 text-emerald-600" />
        The image is built and the service is registered at zero tasks. The first
        person to open it waits ~30–60 s for a cold start.
      </p>
    </Notice>
  )
}

/**
 * The log tail, newest at the bottom, scrolled to the bottom the way a
 * terminal is. Shown while building as well as on failure — watching the
 * package installs go past is the only honest sense of progress there is.
 */
function LogTail({ status }: { status: BuildStatus }) {
  const box = useRef<HTMLPreElement>(null)
  const lines = status.log_tail ?? []

  useEffect(() => {
    const el = box.current
    if (el) el.scrollTop = el.scrollHeight
  }, [lines.length])

  return (
    <SectionCard
      title="Build log"
      description="The tail of the CodeBuild log, as of the last check."
      actions={
        status.log_url ? (
          <ButtonLink
            href={status.log_url}
            variant="ghost"
            size="sm"
            target="_blank"
            rel="noopener"
          >
            <ExternalIcon className="h-4 w-4" />
            Console
          </ButtonLink>
        ) : null
      }
    >
      {lines.length === 0 ? (
        <p className="px-5 py-8 text-center text-sm text-faint">
          No output yet. CodeBuild is still provisioning its own container.
        </p>
      ) : (
        <pre
          ref={box}
          aria-label="Build log tail"
          className="max-h-80 overflow-auto bg-ink/[0.03] px-5 py-4 font-mono text-xs leading-relaxed text-muted"
        >
          {lines.join('\n')}
        </pre>
      )}
    </SectionCard>
  )
}

/**
 * Seconds since the build started. Anchored to `started_at` when the API
 * sends one, so a tab left open overnight does not report a number that
 * drifted; `elapsed_s` is the fallback and the clock ticks locally between
 * polls so the number never looks frozen.
 */
function useElapsed(status: BuildStatus): number {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])

  if (status.started_at) {
    return Math.max(0, Math.floor(now / 1000 - status.started_at))
  }
  return Math.max(0, Math.floor(status.elapsed_s ?? 0))
}

export function formatElapsed(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, '0')}s`
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`
}
