import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ApiError, api } from '../api/client'
import type { AppCost, CostPeriod, CostsReport } from '../api/types'
import { MetaRow, MetaRows } from '../components/MetaRow'
import { Monogram } from '../components/Monogram'
import { StatTile } from '../components/StatTile'
import { Tabs } from '../components/Tabs'
import {
  EmptyState,
  ErrorState,
  Loading,
  Notice,
  PageHeader,
  Panel,
  SectionCard,
} from '../components/states'
import {
  AlertIcon,
  BoltIcon,
  ClockIcon,
  InfoIcon,
  SlidersIcon,
} from '../components/icons'
import { useResource } from '../hooks/useResource'
import {
  describeAnomalies,
  formatHours,
  formatMoney,
  formatMonth,
  formatRate,
  formatUnitRate,
} from '../lib/money'
import { relativeTime } from '../lib/time'

type PeriodKey = 'month_to_date' | 'previous_month'

/**
 * What each app costs, from data we already own.
 *
 * There is deliberately no chart. The platform keeps two months of rollups
 * and nothing else, and a trend line drawn through two points is decoration
 * pretending to be analysis. The numbers are the finding.
 *
 * Two rules this screen exists to keep honest, both from portal-api.md:
 *
 * - Every figure is an ESTIMATE derived from recorded awake time, and the
 *   page says so above the fold, not in a footnote.
 * - Shared overhead is its own line, never divided across apps. A per-app
 *   share of a load balancer would be a number invented by division, and the
 *   reader cannot tell an invented number from a measured one once they are
 *   in the same column.
 */
export function CostsPage() {
  const { data, error, loading, reload } = useResource('costs', (signal) =>
    api.costs(signal),
  )
  const [period, setPeriod] = useState<PeriodKey>('month_to_date')

  if (loading) return <Loading label="Working out costs" />
  if (error) return <ErrorState error={error} onRetry={() => reload()} />
  if (!data) {
    return (
      <ErrorState
        error={new ApiError(0, 'The portal service returned no cost data.')}
        onRetry={() => reload()}
      />
    )
  }

  // A backend that answered 200 with half a payload should degrade, not crash
  // the screen: every accessor below goes through this.
  const block = safePeriod(data[period]) ?? safePeriod(data.month_to_date)
  if (!block) {
    return (
      <ErrorState
        error={new ApiError(0, 'The cost report came back in a shape this page cannot read.')}
        onRetry={() => reload()}
      />
    )
  }
  const shared = block.complete
    ? block.overhead.monthly_total
    : block.overhead.to_date_total

  return (
    <>
      <PageHeader
        title="Costs"
        description="What each app costs to run, derived from the time its task was actually awake."
        meta={`${data.rates.region} Fargate rates: ${formatUnitRate(
          data.rates.vcpu_hour,
        )} per vCPU-hour, ${formatUnitRate(data.rates.gb_hour)} per GB-hour.`}
      />

      <div className="space-y-5">
        <Notice tone="info" title="These are estimates, not billed amounts">
          {data.disclaimer}
        </Notice>

        {data.stale ? (
          <Notice tone="warning" title="Some usage data could not be read">
            The per-app figures below may be incomplete. The shared line is
            unaffected — it does not depend on recorded usage.
          </Notice>
        ) : null}

        <Tabs
          label="Billing period"
          value={period}
          onChange={setPeriod}
          tabs={[
            {
              key: 'month_to_date',
              label: `${formatMonth(data.month_to_date.month)} to date`,
            },
            {
              key: 'previous_month',
              label: formatMonth(data.previous_month.month),
            },
          ]}
        />

        <div className="grid gap-3 sm:grid-cols-3">
          <StatTile
            label="App compute"
            value={formatMoney(block.apps_total)}
            icon={<BoltIcon className="h-4 w-4" />}
            iconClass="bg-emerald-50 dark:bg-emerald-400/10 text-emerald-700 dark:text-emerald-300"
          />
          <StatTile
            label="Shared platform"
            value={formatMoney(shared)}
            icon={<SlidersIcon className="h-4 w-4" />}
            iconClass="bg-azure/10 text-azure"
          />
          <StatTile
            label={block.complete ? 'Estimated total' : 'Estimated so far'}
            value={formatMoney(block.total)}
            icon={<ClockIcon className="h-4 w-4" />}
          />
        </div>

        <AppCostTable period={block} />
        <OverheadCard period={block} />
      </div>
    </>
  )
}

/**
 * Fill in what a partial payload left out.
 *
 * Not defensive programming for its own sake: `stale: true` is a documented
 * state on this endpoint, and a period that came back without its `apps`
 * array should render "nothing to show" rather than throw on `.map`.
 */
function safePeriod(period: CostPeriod | undefined | null): CostPeriod | null {
  if (!period || typeof period !== 'object') return null
  const overhead = period.overhead
  return {
    ...period,
    month: period.month ?? '',
    apps: Array.isArray(period.apps) ? period.apps : [],
    apps_total: Number(period.apps_total) || 0,
    total: Number(period.total) || 0,
    overhead: {
      shared: true,
      lines: Array.isArray(overhead?.lines) ? overhead.lines : [],
      monthly_total: Number(overhead?.monthly_total) || 0,
      to_date_total: Number(overhead?.to_date_total) || 0,
      elapsed_fraction: Number(overhead?.elapsed_fraction) || 0,
      note: overhead?.note ?? '',
    },
  }
}

/** Per app: awake hours, estimated cost, last run. */
function AppCostTable({ period }: { period: CostPeriod }) {
  if (period.apps.length === 0) {
    return (
      <EmptyState title="No apps to cost">
        <p>
          Nothing in <code className="font-mono text-xs">shiny-proxy-apps</code> to
          attribute compute to. The shared platform line below still applies.
        </p>
      </EmptyState>
    )
  }

  const quiet = period.apps.every((app) => app.awake_hours <= 0)

  return (
    <SectionCard
      title="By app"
      description={
        period.complete
          ? `Awake time and estimated compute for ${formatMonth(period.month)}.`
          : `Awake time and estimated compute so far this month. Fargate bills per second, so this tracks the task, not the calendar.`
      }
    >
      {quiet ? (
        <p className="px-5 py-6 text-sm text-muted-foreground">
          No app was awake in this period, so no compute was billed. Every app
          slept the whole time — which is the platform working as designed.
        </p>
      ) : null}

      <div role="table" aria-label="Cost by app">
        <div
          role="row"
          className="grid grid-cols-[minmax(0,1fr)_5.5rem_6rem] gap-3 border-b border-border bg-muted/40 px-5 py-2 text-xs font-medium text-muted-foreground sm:grid-cols-[minmax(0,1fr)_7rem_6rem_7rem]"
        >
          <span role="columnheader">App</span>
          <span role="columnheader" className="hidden sm:block">
            Last run
          </span>
          <span role="columnheader" className="text-right">
            Awake
          </span>
          <span role="columnheader" className="text-right">
            Estimated
          </span>
        </div>

        <div className="divide-y divide-border/60">
          {period.apps.map((app) => (
            <AppCostRow key={app.host} app={app} />
          ))}
        </div>

        <div
          role="row"
          className="grid grid-cols-[minmax(0,1fr)_5.5rem_6rem] gap-3 border-t border-border bg-muted/30 px-5 py-2.5 text-sm sm:grid-cols-[minmax(0,1fr)_7rem_6rem_7rem]"
        >
          <span role="cell" className="font-medium text-foreground">
            App compute
          </span>
          <span role="cell" className="hidden sm:block" />
          <span role="cell" />
          <span
            role="cell"
            className="text-right font-mono font-semibold text-foreground"
          >
            {formatMoney(period.apps_total)}
          </span>
        </div>
      </div>
    </SectionCard>
  )
}

function AppCostRow({ app }: { app: AppCost }) {
  const notes = describeAnomalies(app.anomalies)

  return (
    <div
      role="row"
      className="grid grid-cols-[minmax(0,1fr)_5.5rem_6rem] items-center gap-3 px-5 py-2.5 sm:grid-cols-[minmax(0,1fr)_7rem_6rem_7rem]"
    >
      <div role="cell" className="flex min-w-0 items-center gap-3">
        <Monogram
          name={app.label || app.host}
          seed={app.host}
          className="hidden h-9 w-14 shrink-0 rounded-md border border-border/60 sm:block"
          textClassName="text-xs"
        />
        <div className="min-w-0">
          <Link
            to={`/admin/apps/${encodeURIComponent(app.host)}`}
            className="block truncate text-sm font-medium text-foreground hover:text-azure hover:underline"
          >
            {app.label || app.host}
          </Link>
          <MetaRows>
            {app.size_known ? (
              <MetaRow icon={<SlidersIcon />} title="Task size and its hourly rate">
                {app.cpu / 1024} vCPU / {app.memory / 1024} GB ·{' '}
                {formatRate(app.hourly_rate)}
              </MetaRow>
            ) : (
              <MetaRow icon={<InfoIcon />} tone="warn">
                Task size unknown — awake time is recorded, cost is not estimated
              </MetaRow>
            )}
            {app.currently_awake ? (
              <MetaRow icon={<BoltIcon />} tone="good">
                Awake now — still accruing
              </MetaRow>
            ) : null}
            {notes.map((note) => (
              <MetaRow key={note} icon={<AlertIcon />} tone="warn">
                {note}
              </MetaRow>
            ))}
          </MetaRows>
        </div>
      </div>

      <span
        role="cell"
        className="hidden whitespace-nowrap text-xs text-muted-foreground sm:block"
      >
        {relativeTime(app.last_run)}
      </span>

      <span
        role="cell"
        className="text-right font-mono text-sm text-muted-foreground"
        title={`${app.awake_hours} hours awake`}
      >
        {formatHours(app.awake_hours)}
      </span>

      <span role="cell" className="text-right font-mono text-sm text-foreground">
        {app.size_known ? formatMoney(app.estimated_cost) : '—'}
      </span>
    </div>
  )
}

/**
 * The honest overhead line. Itemised, labelled shared, and NOT spread across
 * the apps above — see the page comment.
 */
function OverheadCard({ period }: { period: CostPeriod }) {
  const { overhead } = period
  const shown = period.complete ? overhead.monthly_total : overhead.to_date_total

  return (
    <SectionCard
      title="Shared platform, itemised"
      description={overhead.note}
      actions={
        <span className="font-mono text-sm font-semibold text-foreground">
          {formatMoney(shown)}
        </span>
      }
    >
      <Panel className="border-0 shadow-none">
        <ul className="divide-y divide-border/60">
          {overhead.lines.map((line) => (
            <li
              key={line.name}
              className="flex items-start justify-between gap-4 px-5 py-2.5"
            >
              <div className="min-w-0">
                <p className="text-sm text-foreground">{line.name}</p>
                <p className="mt-0.5 text-xs text-muted-foreground/80">{line.note}</p>
              </div>
              <span className="shrink-0 font-mono text-sm text-muted-foreground">
                {formatMoney(line.monthly)}
                <span className="ml-1 text-xs text-muted-foreground/70">/mo</span>
              </span>
            </li>
          ))}
        </ul>
      </Panel>

      {period.complete ? null : (
        <p className="border-t border-border/60 px-5 py-3 text-xs text-muted-foreground/80">
          Shown pro-rated to the {Math.round(overhead.elapsed_fraction * 100)}% of{' '}
          {formatMonth(period.month)} elapsed so far —{' '}
          {formatMoney(overhead.monthly_total)} for a full month. These are
          time-based fixed charges, so pro-rating them is arithmetic; splitting
          them between apps would not be.
        </p>
      )}
    </SectionCard>
  )
}

/** Exported for the report header on other screens, if one is ever wanted. */
export type { CostsReport }
