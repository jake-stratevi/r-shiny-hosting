// The costs payload for `npm run dev:mock`. Kept out of mock.ts so the
// fixture arithmetic is readable, and derived from the SAME rates the service
// uses (portal-api.md "Costs") rather than from typed-in dollar amounts — a
// mock whose numbers cannot be reproduced teaches the wrong shape.

import type { App, AppCost, CostPeriod, CostsReport, OverheadLine } from './types'

const VCPU_HOUR = 0.04048
const GB_HOUR = 0.004445
const HOURS_PER_MONTH = 730

const hourlyRate = (cpu: number, memory: number) =>
  (cpu / 1024) * VCPU_HOUR + (memory / 1024) * GB_HOUR

const OVERHEAD: OverheadLine[] = [
  {
    name: 'Application Load Balancer',
    monthly: round(0.0225 * HOURS_PER_MONTH),
    note: 'Always on. $0.0225/hr x 730. The platform’s fixed floor.',
  },
  {
    name: 'ALB capacity units',
    monthly: 3,
    note: 'LCU-hours at $0.008. Approximate — scales with traffic.',
  },
  {
    name: 'Authorizing proxy task',
    monthly: round(hourlyRate(256, 512) * HOURS_PER_MONTH),
    note: '0.25 vCPU / 512 MB, always on (ADR-0014). Never sleeps.',
  },
  {
    name: 'Route 53 hosted zone',
    monthly: 0.5,
    note: 'tools.stratevi.com. Query charges are pennies.',
  },
  {
    name: 'DynamoDB, S3 and CloudWatch',
    monthly: 1,
    note: 'Two small tables, the uploads bucket, log retention.',
  },
]

function round(value: number): number {
  return Math.round(value * 100) / 100
}

/** A plausible awake-hours figure per app, stable across reloads. */
function hoursFor(host: string, scale: number): number {
  let hash = 0
  for (const ch of host) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0
  return round(((hash % 90) / 3) * scale)
}

function appCost(app: App, month: string, scale: number, now: number): AppCost {
  // The admin app object carries no cpu/memory (portal-api.md "Shapes") — the
  // service reads them off the DynamoDB row. The mock picks a plausible size
  // from the app key, which is exactly what the service's own fallback does
  // for a row created before the portal existed.
  const big = app.app_key === 'model'
  const cpu = big ? 4096 : 512
  const memory = big ? 16384 : 2048
  const hours = app.status === 'building' ? 0 : hoursFor(app.host, scale)
  const rate = hourlyRate(cpu, memory)

  return {
    host: app.host,
    label: app.label || app.host,
    app_key: app.app_key,
    cpu,
    memory,
    size_known: true,
    hourly_rate: round6(rate),
    awake_hours: hours,
    estimated_cost: round(hours * rate),
    last_run: hours > 0 ? now - 4 * 3600 : null,
    currently_awake: app.live_state === 'awake',
    daily:
      hours > 0
        ? [
            { day: `${month}-02`, awake_hours: round(hours * 0.4) },
            { day: `${month}-09`, awake_hours: round(hours * 0.6) },
          ]
        : [],
    anomalies: app.app_key === 'model' ? { unclosed: 1 } : {},
  }
}

const round6 = (v: number) => Math.round(v * 1_000_000) / 1_000_000

function period(
  apps: App[],
  month: string,
  scale: number,
  complete: boolean,
  now: number,
): CostPeriod {
  const lines = apps.map((app) => appCost(app, month, scale, now))
  lines.sort((a, b) => b.estimated_cost - a.estimated_cost)

  const appsTotal = round(lines.reduce((sum, line) => sum + line.estimated_cost, 0))
  const monthly = round(OVERHEAD.reduce((sum, line) => sum + line.monthly, 0))
  const fraction = complete ? 1 : 0.52
  const toDate = round(monthly * fraction)

  return {
    month,
    start: 0,
    end: 0,
    complete,
    apps: lines,
    apps_total: appsTotal,
    overhead: {
      shared: true,
      lines: OVERHEAD,
      monthly_total: monthly,
      to_date_total: toDate,
      elapsed_fraction: fraction,
      note:
        'Shared by every app and attributable to none. Not divided across apps: ' +
        'a per-app share of a load balancer is a number invented by division.',
    },
    total: round(appsTotal + (complete ? monthly : toDate)),
  }
}

export function mockCosts(apps: App[]): CostsReport {
  const now = Math.floor(Date.now() / 1000)
  const today = new Date()
  const thisMonth = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}`
  const last = new Date(today.getFullYear(), today.getMonth() - 1, 1)
  const lastMonth = `${last.getFullYear()}-${String(last.getMonth() + 1).padStart(2, '0')}`

  return {
    currency: 'USD',
    basis: 'awake_time',
    generated_at: now,
    stale: false,
    rates: {
      vcpu_hour: VCPU_HOUR,
      gb_hour: GB_HOUR,
      region: 'us-east-1',
      source: 'AWS Fargate on-demand pricing, Linux/X86, us-east-1.',
    },
    disclaimer:
      'Estimates derived from recorded awake time and published Fargate rates, ' +
      'not billed amounts. AWS Cost Explorer remains the source of truth for an invoice.',
    month_to_date: period(apps, thisMonth, 0.45, false, now),
    previous_month: period(apps, lastMonth, 1, true, now),
  }
}
