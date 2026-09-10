/**
 * Placeholder art for an app, ported from assembled.work's PreviewCard: two
 * letters on a tint chosen deterministically from the app's key, so an app
 * keeps the same colour everywhere it appears and the grid is scannable
 * before anyone has read a word.
 *
 * They fall back to this only when no screenshot exists. We have no
 * screenshots at all — a Shiny app behind Cognito can't be thumbnailed
 * without a headless browser — so the monogram IS the art here.
 */

/**
 * Cool hues only, deliberately. Emerald, amber and red are the status
 * language (awake / starting / expired); if a card's decorative art could be
 * any of them, a glance at the grid stops being trustworthy. So the art gets
 * the blues, violets and greys, and never a warm tint.
 */
const TINTS = [
  'bg-slate-100 text-slate-600',
  'bg-sky-100 text-sky-800',
  'bg-indigo-100 text-indigo-800',
  'bg-violet-100 text-violet-800',
  'bg-cyan-100 text-cyan-800',
  'bg-zinc-200 text-zinc-600',
]

export function tintFor(seed: string): string {
  let hash = 0
  for (const ch of seed) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0
  return TINTS[hash % TINTS.length]
}

/**
 * "Treatment Pathway Dashboard" -> "TP"; "Model" -> "MO";
 * "Forecast (2025 archive)" -> "FA" — punctuation and bare numbers are
 * skipped, because "F(" is not a monogram.
 */
export function monogramFor(name: string): string {
  const cleaned = name
    .trim()
    .split(/\s+/)
    .map((word) => word.replace(/[^\p{L}\p{N}]/gu, ''))
    .filter(Boolean)
  const lettered = cleaned.filter((word) => /^\p{L}/u.test(word))
  const words = lettered.length > 0 ? lettered : cleaned
  if (words.length === 0) return '??'
  const first = words[0][0] ?? ''
  const second = words[1]?.[0] ?? words[0][1] ?? ''
  return (first + second).toUpperCase()
}

export function Monogram({
  name,
  seed,
  className = '',
  textClassName = 'text-2xl',
}: {
  name: string
  /** Stable identity for the colour — the host or app key, never the label. */
  seed: string
  className?: string
  textClassName?: string
}) {
  return (
    <div
      aria-hidden="true"
      className={`flex items-center justify-center ${tintFor(seed)} ${className}`}
    >
      <span className={`font-semibold tracking-tight ${textClassName}`}>
        {monogramFor(name)}
      </span>
    </div>
  )
}
