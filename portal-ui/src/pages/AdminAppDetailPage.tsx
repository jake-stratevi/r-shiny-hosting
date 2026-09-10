import { useEffect, useState, type ReactNode } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import type { App } from '../api/types'
import { Button, ButtonLink, ButtonRoute } from '../components/Button'
import { Monogram } from '../components/Monogram'
import { StatusBadge } from '../components/StatusBadge'
import { Tabs, TabPanel } from '../components/Tabs'
import { ErrorState, Loading, Notice, Panel } from '../components/states'
import {
  ArrowLeftIcon,
  CheckIcon,
  CopyIcon,
  ExternalIcon,
  HistoryIcon,
  SlidersIcon,
} from '../components/icons'
import { useResource } from '../hooks/useResource'
import {
  accessSummary,
  expiryPhrase,
  expirySummary,
  liveStateHint,
  needsAttention,
} from '../lib/appDisplay'
import { formatDateTime, relativeTime } from '../lib/time'
import { AppSettingsForm } from './AppSettingsForm'
import { AuditLog } from './AuditLog'

type Tab = 'settings' | 'audit'

const TABS = [
  { key: 'settings' as const, label: 'Settings', icon: <SlidersIcon className="h-4 w-4" /> },
  { key: 'audit' as const, label: 'Audit log', icon: <HistoryIcon className="h-4 w-4" /> },
]

export function AdminAppDetailPage() {
  const { host = '' } = useParams<{ host: string }>()
  const [params, setParams] = useSearchParams()
  const tab: Tab = params.get('tab') === 'audit' ? 'audit' : 'settings'

  const { data, error, loading, reload } = useResource(`app:${host}`, (signal) =>
    api.app(host, signal),
  )

  // Held locally so a PATCH response updates the page without a refetch.
  const [app, setApp] = useState<App | null>(null)
  useEffect(() => setApp(data), [data])

  if (loading) return <Loading label="Loading app" />
  if (error) return <ErrorState error={error} onRetry={() => reload()} />
  if (!app) return null

  return (
    <>
      <nav className="mb-4">
        <Link
          to="/admin"
          className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-accent"
        >
          <ArrowLeftIcon className="h-4 w-4" />
          All apps
        </Link>
      </nav>

      <Header app={app} />
      <StateNotice app={app} />

      <div className="mt-6 grid items-start gap-6 xl:grid-cols-detail">
        <div className="min-w-0 space-y-5">
          <Tabs
            tabs={TABS}
            value={tab}
            label="App sections"
            onChange={(next) =>
              setParams(next === 'settings' ? {} : { tab: next }, { replace: true })
            }
          />

          <TabPanel tabKey="settings" active={tab === 'settings'}>
            <AppSettingsForm key={app.host} app={app} onSaved={setApp} />
          </TabPanel>
          <TabPanel tabKey="audit" active={tab === 'audit'}>
            <AuditLog host={app.host} />
          </TabPanel>
        </div>

        <AtAGlance app={app} />
      </div>
    </>
  )
}

/**
 * assembled.work's Show header: the app's face, its name and live state, a
 * dot-separated meta line answering the questions people arrive with, and
 * the two actions worth a click on every visit.
 */
function Header({ app }: { app: App }) {
  const label = app.label || app.host
  const expiry = expirySummary(app.expires_at)
  const url = `https://${app.host}`

  return (
    <div className="flex flex-wrap items-center justify-between gap-4">
      <div className="flex min-w-0 items-center gap-4">
        <Monogram
          name={label}
          seed={app.host}
          className="hidden h-14 w-20 shrink-0 rounded-card border border-line sm:flex"
          textClassName="text-lg"
        />
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="truncate text-2xl font-semibold tracking-tight text-ink">{label}</h1>
            <StatusBadge state={app.live_state} title={liveStateHint(app.live_state) ?? undefined} />
          </div>
          <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-sm text-muted">
            <span className="font-mono text-xs">{app.host}</span>
            <Dot />
            <span>{accessSummary(app)}</span>
            <Dot />
            <span
              className={
                expiry.tone === 'past' || expiry.tone === 'soon'
                  ? 'font-medium text-amber-800'
                  : undefined
              }
            >
              {expiryPhrase(expiry)}
            </span>
            <Dot />
            <span>Last active {relativeTime(app.last_active)}</span>
          </p>
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-2">
        <ButtonLink href={url} variant="outline" target="_blank" rel="noopener">
          <ExternalIcon className="h-4 w-4" />
          Open app
        </ButtonLink>
        <CopyLinkButton url={url} />
      </div>
    </div>
  )
}

const Dot = () => (
  <span aria-hidden="true" className="text-faint">
    ·
  </span>
)

function CopyLinkButton({ url }: { url: string }) {
  const [copied, setCopied] = useState(false)

  async function copy() {
    try {
      await navigator.clipboard.writeText(url)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // No clipboard permission (or no clipboard at all, in an old browser).
      // The URL is on screen in the header, so this fails quietly.
    }
  }

  return (
    <Button variant="ghost" onClick={() => void copy()}>
      {copied ? <CheckIcon className="h-4 w-4" /> : <CopyIcon className="h-4 w-4" />}
      {copied ? 'Copied' : 'Copy link'}
    </Button>
  )
}

/** One state-driven card, only when something is actually the matter. */
function StateNotice({ app }: { app: App }) {
  // P2a: a build in flight is not "the matter", but it does explain why the
  // app answers nothing yet, and the build screen is where to watch it.
  if (app.status === 'building' || app.live_state === 'building') {
    return (
      <div className="mt-5">
        <Notice
          tone="info"
          title="This app is still building"
          action={
            <ButtonRoute to={`/admin/apps/${encodeURIComponent(app.host)}/build`}>
              Watch the build
            </ButtonRoute>
          }
        >
          Its image is being built. First builds take 10–20 minutes because R
          packages compile from source.
        </Notice>
      </div>
    )
  }

  if (app.status === 'build_failed' || app.live_state === 'build_failed') {
    return (
      <div className="mt-5">
        <Notice
          tone="danger"
          title="This app's build failed"
          action={
            <ButtonRoute
              to={`/admin/apps/${encodeURIComponent(app.host)}/build`}
              variant="outline"
            >
              See the build log
            </ButtonRoute>
          }
        >
          It has never run, so there is nothing to open. The row and the
          half-built resources were kept deliberately, for inspection.
        </Notice>
      </div>
    )
  }

  const flag = needsAttention(app)
  if (!flag) return null

  if (app.status === 'expired' || app.live_state === 'expired') {
    return (
      <div className="mt-5">
        <Notice tone="danger" title="This app has expired">
          The proxy refuses every request to it and its task is scaled to zero. Give it a future
          expiry date below to bring it back.
        </Notice>
      </div>
    )
  }

  if (app.status === 'disabled') {
    return (
      <div className="mt-5">
        <Notice tone="warning" title="This app is disabled">
          Anyone who opens it gets the refusal page. Nothing has been deleted — set the status back
          to active below.
        </Notice>
      </div>
    )
  }

  if (app.access_mode === 'users' && app.allowed_emails.length === 0) {
    return (
      <div className="mt-5">
        <Notice tone="warning" title="Nobody can open this app">
          Its access mode is a list of addresses and the list is empty. Add someone below, or
          switch it to everyone signed in.
        </Notice>
      </div>
    )
  }

  if (app.access_mode !== 'all_users' && app.access_mode !== 'users') {
    return (
      <div className="mt-5">
        <Notice tone="warning" title="Reserved access mode">
          <code className="font-mono text-xs">{String(app.access_mode)}</code> is not implemented,
          so the proxy fails closed on every request. Pick one of the two supported modes below.
        </Notice>
      </div>
    )
  }

  return (
    <div className="mt-5">
      <Notice tone="warning" title={flag}>
        Extend the expiry below if this app is still in use.
      </Notice>
    </div>
  )
}

/**
 * The answers people come to this page for, in one rail. Ported from
 * assembled.work's "At a glance" card; the rows they make into doors are
 * plain here, because everything they link to is already on this page.
 */
function AtAGlance({ app }: { app: App }) {
  const expiry = expirySummary(app.expires_at)

  return (
    <Panel className="overflow-hidden xl:sticky xl:top-20">
      <div className="border-b border-line-soft px-5 py-3 text-sm font-semibold text-ink">
        At a glance
      </div>
      <dl className="divide-y divide-line-soft text-sm">
        <GlanceRow term="Status">
          <StatusBadge state={app.live_state} />
        </GlanceRow>
        <GlanceRow term="Who can open it">{accessSummary(app)}</GlanceRow>
        <GlanceRow term="Expires">
          <span
            className={
              expiry.tone === 'soon' || expiry.tone === 'past'
                ? 'font-medium text-amber-800'
                : undefined
            }
          >
            {expiry.text}
          </span>
        </GlanceRow>
        <GlanceRow term="Last active">{relativeTime(app.last_active)}</GlanceRow>
        <GlanceRow term="Awake since">
          {app.awake_since ? formatDateTime(app.awake_since) : '—'}
        </GlanceRow>
        <GlanceRow term="Tasks">
          <span className="font-mono text-xs">
            {app.running_count}/{app.desired_count}
          </span>
        </GlanceRow>
        <GlanceRow term="Idle timeout">{app.idle_minutes} min</GlanceRow>
        <GlanceRow term="Session cap">
          {app.max_session_hours === 0 ? 'Uncapped' : `${app.max_session_hours} h`}
        </GlanceRow>
        <GlanceRow term="Key">
          <span className="font-mono text-xs">{app.app_key || '—'}</span>
        </GlanceRow>
        <GlanceRow term="ECS service">
          <span className="truncate font-mono text-xs" title={app.ecs_service}>
            {app.ecs_service || '—'}
          </span>
        </GlanceRow>
        <GlanceRow term="Container port">
          <span className="font-mono text-xs">{app.container_port ?? '—'}</span>
        </GlanceRow>
      </dl>
    </Panel>
  )
}

function GlanceRow({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 px-5 py-2.5">
      <dt className="shrink-0 text-muted">{term}</dt>
      <dd className="min-w-0 truncate text-right text-ink">{children}</dd>
    </div>
  )
}
