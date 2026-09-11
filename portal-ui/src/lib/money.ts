// Formatting for the costs screen. Everything on the wire is a plain number
// of USD or of hours (portal-api.md "Costs"); everything shown to a human is
// formatted here, in one place, so two tiles cannot disagree about rounding.

/**
 * `$0.36`, `$29.94`, `$1,204.10`.
 *
 * Sub-cent amounts render as `<$0.01` rather than `$0.00`: an app that ran
 * for ninety seconds did cost something, and a screen that says `$0.00` next
 * to "0.03 hours" reads as a bug rather than as a small number.
 */
export function formatMoney(amount: number | null | undefined): string {
  if (amount === null || amount === undefined || !Number.isFinite(amount)) return '—'
  if (amount === 0) return '$0.00'
  if (Math.abs(amount) < 0.005) return '<$0.01'
  return `$${Math.abs(amount).toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`
}

/** `$0.0291/hr` — the per-hour rate, which needs more precision than a total. */
export function formatRate(rate: number | null | undefined): string {
  if (rate === null || rate === undefined || !Number.isFinite(rate)) return '—'
  return `$${rate.toFixed(4)}/hr`
}

/**
 * `$0.04048`, `$0.004445` — a per-UNIT rate (per vCPU-hour, per GB-hour).
 *
 * Separate from `formatRate` because these are the published AWS constants,
 * and rounding `0.004445` to four places prints `0.0044`, which no longer
 * matches the number anyone would look up. Trailing zeros are trimmed so a
 * round rate does not pretend to six digits of precision.
 */
export function formatUnitRate(rate: number | null | undefined): string {
  if (rate === null || rate === undefined || !Number.isFinite(rate)) return '—'
  return `$${rate.toFixed(6).replace(/0+$/, '').replace(/\.$/, '')}`
}

/**
 * `0h`, `18 min`, `2.4h`, `124h`.
 *
 * Under an hour is shown in minutes because "0.03h" is not a duration anybody
 * reads; above ten hours the decimal is dropped for the same reason.
 */
export function formatHours(hours: number | null | undefined): string {
  if (hours === null || hours === undefined || !Number.isFinite(hours)) return '—'
  if (hours <= 0) return '0h'
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))} min`
  if (hours < 10) return `${hours.toFixed(1)}h`
  return `${Math.round(hours)}h`
}

/** `September 2026` from the contract's `YYYY-MM`. */
export function formatMonth(month: string): string {
  const [year, index] = (month ?? '').split('-')
  const at = Number(index) - 1
  if (!year || !MONTHS[at]) return month || '—'
  return `${MONTHS[at]} ${year}`
}

const MONTHS = [
  'January',
  'February',
  'March',
  'April',
  'May',
  'June',
  'July',
  'August',
  'September',
  'October',
  'November',
  'December',
]

/**
 * The degenerate audit-event counts, in words. An open map on the wire, so
 * an unknown key falls back to its own name rather than being dropped — the
 * reader should see that something was odd even if this build has no phrase
 * for it.
 */
const ANOMALY_TEXT: Record<string, (n: number) => string> = {
  unclosed: (n) =>
    n === 1
      ? '1 run never recorded stopping and was capped — the real figure may be higher'
      : `${n} runs never recorded stopping and were capped — the real figure may be higher`,
  duplicate_wake: (n) => `${n} repeated start event${n === 1 ? '' : 's'} ignored`,
  orphan_close: (n) => `${n} stop event${n === 1 ? '' : 's'} with no matching start`,
  awake_at_window_start: (n) =>
    `${n} run${n === 1 ? '' : 's'} began before this period and were estimated from its start`,
  undated: (n) => `${n} event${n === 1 ? '' : 's'} had no usable timestamp`,
  nonpositive: (n) => `${n} zero-length or backwards interval${n === 1 ? '' : 's'}`,
}

export function describeAnomalies(
  anomalies: Record<string, number> | null | undefined,
): string[] {
  if (!anomalies) return []
  return Object.entries(anomalies)
    .filter(([, count]) => Number(count) > 0)
    .map(([name, count]) =>
      ANOMALY_TEXT[name] ? ANOMALY_TEXT[name](Number(count)) : `${name}: ${count}`,
    )
}
