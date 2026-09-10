// Everything on the wire is epoch SECONDS (portal-api.md "Shapes").
// Everything shown to a human is in the browser's local timezone.

const MINUTE = 60
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

/** "just now" / "4 min ago" / "in 6 days" / "never". */
export function relativeTime(epochSeconds: number | null | undefined, now = Date.now()): string {
  if (epochSeconds === null || epochSeconds === undefined) return 'never'
  const delta = Math.round(epochSeconds - now / 1000)
  const ago = delta < 0
  const s = Math.abs(delta)

  let text: string
  if (s < 45) text = 'just now'
  else if (s < 90) text = 'a minute'
  else if (s < HOUR) text = `${Math.round(s / MINUTE)} min`
  else if (s < 22 * HOUR) text = plural(Math.round(s / HOUR), 'hour')
  else if (s < 30 * DAY) text = plural(Math.round(s / DAY), 'day')
  else if (s < 365 * DAY) text = plural(Math.round(s / (30 * DAY)), 'month')
  else text = plural(Math.round(s / (365 * DAY)), 'year')

  if (text === 'just now') return text
  return ago ? `${text} ago` : `in ${text}`
}

function plural(n: number, unit: string): string {
  return `${n} ${unit}${n === 1 ? '' : 's'}`
}

/** "10 Sep 2026, 14:32" — unambiguous, no locale-order surprises. */
export function formatDateTime(epochSeconds: number | null | undefined): string {
  if (epochSeconds === null || epochSeconds === undefined) return '—'
  const d = new Date(epochSeconds * 1000)
  return `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}, ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** "10 Sep 2026" */
export function formatDate(epochSeconds: number | null | undefined): string {
  if (epochSeconds === null || epochSeconds === undefined) return 'Never'
  const d = new Date(epochSeconds * 1000)
  return `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const pad = (n: number) => String(n).padStart(2, '0')

/** Epoch seconds -> the `YYYY-MM-DD` an `<input type="date">` wants, local. */
export function epochToDateInput(epochSeconds: number | null): string {
  if (epochSeconds === null) return ''
  const d = new Date(epochSeconds * 1000)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/**
 * `YYYY-MM-DD` -> epoch seconds at the END of that local day (23:59:59), so
 * "expires 30 Sep" means the app still works all day on the 30th. See the
 * README's contract note: portal-api.md does not pin this down.
 */
export function dateInputToEpoch(value: string): number | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)
  if (!m) return null
  const [, y, mo, d] = m
  const date = new Date(Number(y), Number(mo) - 1, Number(d), 23, 59, 59, 0)
  if (Number.isNaN(date.getTime())) return null
  return Math.floor(date.getTime() / 1000)
}

/** Today's date in the `<input type="date">` format, for `min=`. */
export function todayDateInput(now = new Date()): string {
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

/** Default offered when someone switches an app off "never expires". */
export function defaultExpiryEpoch(days = 30, now = new Date()): number {
  const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() + days, 23, 59, 59, 0)
  return Math.floor(d.getTime() / 1000)
}
