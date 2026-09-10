// Client-side bundle inspection: what is in the .zip the user just chose,
// read in the browser BEFORE the upload starts.
//
// Why here and not on the server: docs/design/portal-api.md, "Bundle
// inspection is CLIENT-SIDE — there is no inspect endpoint". The proxy task
// is in the request path for every app on the platform, so making it
// download and unzip a 100 MB bundle would put other people's page loads
// behind that work on 0.25 vCPU. The browser already holds the bytes, and
// inspecting before the upload also fails faster.
//
// This is a UX affordance, NOT a security control. CodeBuild's
// `proxy-app/buildspec/validate.py` re-checks the bundle server-side and is
// the only authority; a client that lies gets a failed build, not a bad app.
// The entrypoint rule below is deliberately a mirror of that file's
// `resolve_entrypoint()` — including its ignored-file list, its "exactly one
// wrapper directory gets flattened" rule, its refusal of two candidate
// directories, and its refusal of a dangling half of the ui.R/server.R pair.
// If you change one, change the other.

/** Where the package list came from; `none` = nothing was found. */
export type PackageSource = 'renv.lock' | 'packages.txt' | 'scan' | 'none'

export interface BundleInspection {
  /** `app.R` or `ui.R+server.R` — validate.py's own two spellings. */
  entrypoint: string
  /** The wrapper directory the build will flatten away, when there is one. */
  wrapper: string | null
  packages: string[]
  source: PackageSource
  /** Things worth saying out loud. Never fatal — the user can edit the list. */
  warnings: string[]
}

/** Inspection could not produce an answer. `message` is shown to the user. */
export class InspectionError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'InspectionError'
  }
}

/**
 * Work caps. A bundle is untrusted input: it can be huge, deeply nested, or
 * deliberately hostile (a few kB of zip that inflates to gigabytes). Nothing
 * here decompresses an entry we did not ask for, and everything that does
 * decompress is bounded, so the worst case is a clear message rather than a
 * frozen tab.
 */
export interface InspectionLimits {
  /** validate.py's own entry ceiling. Over it, the build would reject anyway. */
  maxEntries: number
  /** Biggest renv.lock / packages.txt we will decompress. */
  maxTextBytes: number
  /** How many .R/.Rmd files the fallback scan will read. */
  maxScanFiles: number
  /** Biggest single source file the scan will read. */
  maxScanFileBytes: number
  /** Total decompressed bytes the scan may read. */
  maxScanTotalBytes: number
  /** Whole-inspection deadline. */
  timeoutMs: number
}

export const DEFAULT_LIMITS: InspectionLimits = {
  maxEntries: 5000,
  maxTextBytes: 1024 * 1024,
  maxScanFiles: 200,
  maxScanFileBytes: 256 * 1024,
  maxScanTotalBytes: 4 * 1024 * 1024,
  timeoutMs: 20_000,
}

// --- the minimum of JSZip's surface we depend on ---------------------------
// Typed here rather than imported so the dynamic import below stays the only
// reference to the package, and so tests can build zips with the same handle.

export interface ZipEntry {
  name: string
  dir: boolean
  async(type: 'string'): Promise<string>
}

export interface LoadedZip {
  files: Record<string, ZipEntry>
}

export interface JSZipLike {
  file(path: string, content: string): unknown
  folder(path: string): unknown
  loadAsync(
    data: ArrayBuffer | Uint8Array,
    options?: { createFolders?: boolean },
  ): Promise<LoadedZip>
  generateAsync(options: { type: 'arraybuffer' }): Promise<ArrayBuffer>
}

export interface JSZipConstructor {
  new (): JSZipLike
}

/**
 * Loaded on demand, so the menu and the admin pages — which the whole team
 * opens and which never inspect a zip — do not carry ~100 kB of zip reader.
 * Vite gives it its own chunk; see README, "Where dist/ goes".
 */
export async function loadJSZip(): Promise<JSZipConstructor> {
  const mod = (await import('jszip')) as unknown as {
    default?: JSZipConstructor
  }
  // UMD via Vite's interop gives `.default`; a plain CJS resolution does not.
  return mod.default ?? (mod as unknown as JSZipConstructor)
}

// --- validate.py's rules, ported -------------------------------------------

const IGNORED_PREFIXES = ['__MACOSX/']
const IGNORED_BASENAMES = new Set(['.DS_Store', 'Thumbs.db', 'desktop.ini'])

const ENTRYPOINT_SINGLE = 'app.R'
const ENTRYPOINT_PAIR = ['ui.R', 'server.R'] as const

/** Written this way so the byte itself never appears in this source file. */
const NUL = String.fromCharCode(0)

/** Zip names are '/'-separated; a backslash is a Windows tool bug or an escape. */
function normalise(name: string): string {
  return name.replace(/\\/g, '/')
}

function isIgnored(name: string): boolean {
  if (IGNORED_PREFIXES.some((p) => name.startsWith(p))) return true
  const trimmed = name.endsWith('/') ? name.slice(0, -1) : name
  const base = trimmed.slice(trimmed.lastIndexOf('/') + 1)
  return IGNORED_BASENAMES.has(base)
}

/** validate.py `check_path`: nothing may resolve outside the bundle root. */
function checkPath(name: string): void {
  if (name.trim() === '') {
    throw new InspectionError('The archive contains an entry with an empty name.')
  }
  if (name.includes(NUL)) {
    throw new InspectionError(`An entry name contains a NUL byte: ${name}`)
  }
  if (name.startsWith('/')) {
    throw new InspectionError(
      `Absolute path in the archive (zip-slip): ${name} — bundle paths must be relative to the bundle root.`,
    )
  }
  if (name.length > 1 && name[1] === ':') {
    throw new InspectionError(
      `Drive-qualified path in the archive (zip-slip): ${name} — re-zip from inside the app folder, not from a drive root.`,
    )
  }
  let depth = 0
  for (const part of name.split('/')) {
    if (part === '' || part === '.') continue
    if (part === '..') {
      depth -= 1
      if (depth < 0) {
        throw new InspectionError(`A path escapes the bundle root (zip-slip): ${name}`)
      }
    } else {
      depth += 1
    }
  }
}

interface Member {
  /** Normalised, '/'-separated, no trailing slash for files. */
  name: string
  dir: boolean
  entry: ZipEntry
}

/** The immediate children of a directory, the way `os.listdir` sees them. */
function listing(members: Member[], prefix: string): Set<string> {
  const names = new Set<string>()
  for (const member of members) {
    if (prefix !== '' && !member.name.startsWith(prefix)) continue
    const rest = member.name.slice(prefix.length)
    const first = rest.split('/')[0]
    if (first !== '') names.add(first)
  }
  return names
}

/** True when this path names a directory (explicitly, or by having children). */
function directoriesIn(members: Member[], prefix: string): Set<string> {
  const dirs = new Set<string>()
  for (const member of members) {
    if (prefix !== '' && !member.name.startsWith(prefix)) continue
    const rest = member.name.slice(prefix.length)
    const parts = rest.split('/')
    if (parts[0] === '') continue
    if (parts.length > 1 || member.dir) dirs.add(parts[0])
  }
  return dirs
}

function findEntrypoint(names: Set<string>): string | null {
  if (names.has(ENTRYPOINT_SINGLE)) return ENTRYPOINT_SINGLE
  if (ENTRYPOINT_PAIR.every((n) => names.has(n))) return 'ui.R+server.R'
  return null
}

function halfPair(names: Set<string>): string | null {
  const present = ENTRYPOINT_PAIR.filter((n) => names.has(n))
  return present.length === 1 ? present[0] : null
}

export interface EntrypointResult {
  entrypoint: string
  /** The single wrapper directory the build flattens, or null. */
  wrapper: string | null
}

/**
 * A port of validate.py's `resolve_entrypoint()`, decided from the zip index
 * instead of an extracted tree. Same order, same refusals, same wording where
 * the wording is what the user has to act on.
 */
export function resolveEntrypoint(members: Member[]): EntrypointResult {
  const root = listing(members, '')

  const found = findEntrypoint(root)
  if (found) return { entrypoint: found, wrapper: null }

  const dangling = halfPair(root)
  if (dangling) {
    const other = ENTRYPOINT_PAIR.find((n) => n !== dangling)
    throw new InspectionError(
      `Found ${dangling} at the bundle root but no ${other} — a two-file Shiny app needs both ui.R and server.R, or a single app.R.`,
    )
  }

  const dirs = [...directoriesIn(members, '')].sort()
  const candidates = dirs.filter((d) => findEntrypoint(listing(members, `${d}/`)))

  if (candidates.length === 1) {
    const wrapper = candidates[0]
    const children = listing(members, `${wrapper}/`)
    for (const child of [...children].sort()) {
      if (root.has(child)) {
        throw new InspectionError(
          `The wrapper directory ${wrapper}/ cannot be flattened: it contains ${child}, which already exists at the bundle root.`,
        )
      }
    }
    const inner = findEntrypoint(children)
    if (!inner) {
      // Unreachable — `candidates` was filtered on exactly this. Belt and braces.
      throw new InspectionError(`No entrypoint inside the wrapper directory ${wrapper}/.`)
    }
    return { entrypoint: inner, wrapper }
  }

  if (candidates.length > 1) {
    throw new InspectionError(
      `More than one directory looks like the app: ${candidates.join(', ')} — zip only the app folder, or put app.R at the bundle root.`,
    )
  }

  const entries = [...root].sort()
  let printed = entries.slice(0, 20).join(', ') || '(empty)'
  if (entries.length > 20) printed += `, … (${entries.length} entries total)`
  throw new InspectionError(
    `No app.R or ui.R/server.R found at the bundle root or in a single wrapper directory — the bundle's top level contains: ${printed}`,
  )
}

// --- packages ---------------------------------------------------------------

/** `renv.lock` -> the keys of its `Packages` object, in file order. */
export function packagesFromRenvLock(text: string): string[] {
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    throw new InspectionError('renv.lock is not valid JSON.')
  }
  const bag =
    parsed && typeof parsed === 'object'
      ? (parsed as { Packages?: unknown }).Packages
      : undefined
  if (!bag || typeof bag !== 'object' || Array.isArray(bag)) {
    throw new InspectionError('renv.lock has no "Packages" section.')
  }
  return dedupe(Object.keys(bag as Record<string, unknown>))
}

/** `packages.txt` -> one name per line; blanks and `#` comments dropped. */
export function packagesFromPackagesTxt(text: string): string[] {
  const out: string[] = []
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.split('#')[0].trim()
    if (line !== '') out.push(line)
  }
  return dedupe(out)
}

const LIBRARY_CALL = /\b(?:library|require)\s*\(\s*(?:"([^"\n]+)"|'([^'\n]+)'|([A-Za-z.][\w.]*))/g
const NAMESPACE_CALL = /\brequireNamespace\s*\(\s*(?:"([^"\n]+)"|'([^'\n]+)')/g

/** `library(x)` / `require("x")` / `requireNamespace("x")` in one source file. */
export function packagesFromSource(text: string): string[] {
  const out: string[] = []
  for (const pattern of [LIBRARY_CALL, NAMESPACE_CALL]) {
    pattern.lastIndex = 0
    let match: RegExpExecArray | null
    while ((match = pattern.exec(text)) !== null) {
      const name = (match[1] ?? match[2] ?? match[3] ?? '').trim()
      if (name !== '') out.push(name)
    }
  }
  return out
}

function dedupe(names: string[]): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const name of names) {
    if (name === '' || seen.has(name)) continue
    seen.add(name)
    out.push(name)
  }
  return out
}

const SOURCE_EXTENSIONS = ['.r', '.rmd']

function isSourceFile(name: string): boolean {
  const lower = name.toLowerCase()
  return SOURCE_EXTENSIONS.some((ext) => lower.endsWith(ext))
}

/** JSZip records the central directory's uncompressed size on a private field. */
function declaredSize(entry: ZipEntry): number | null {
  const data = (entry as unknown as { _data?: { uncompressedSize?: unknown } })._data
  const size = data?.uncompressedSize
  return typeof size === 'number' && Number.isFinite(size) ? size : null
}

// --- the whole job ----------------------------------------------------------

export interface InspectOptions {
  limits?: Partial<InspectionLimits>
}

/**
 * Read the chosen file's zip index and answer: which entrypoint the build
 * will use, which packages the app declares, and where that list came from.
 *
 * Throws `InspectionError` with a sentence the user can act on. Callers must
 * treat that as "say why, then let them type the list by hand" — never as a
 * reason to block the upload, because validate.py, not this, decides.
 */
export async function inspectBundle(
  file: File,
  options: InspectOptions = {},
): Promise<BundleInspection> {
  const limits: InspectionLimits = { ...DEFAULT_LIMITS, ...options.limits }
  const expiresAt = Date.now() + limits.timeoutMs
  let timer: ReturnType<typeof setTimeout> | undefined

  // Two belts. The timer catches a single call that never returns (a
  // pathological inflate); `ensureTime` catches the slow accumulation of
  // many small reads, and makes the bail-out deterministic to test.
  const deadline = new Promise<never>((_resolve, reject) => {
    timer = setTimeout(() => reject(timedOut()), limits.timeoutMs)
  })

  const ensureTime = () => {
    if (Date.now() >= expiresAt) throw timedOut()
  }

  try {
    return await Promise.race([run(file, limits, ensureTime), deadline])
  } finally {
    if (timer !== undefined) clearTimeout(timer)
  }
}

function timedOut(): InspectionError {
  return new InspectionError(
    'Reading the bundle took too long, so it was stopped. Nothing is being guessed — list the packages below.',
  )
}

/**
 * `Blob.arrayBuffer()` where it exists, `FileReader` where it does not —
 * older Safari, and jsdom, which the tests run in.
 */
function readBytes(file: File): Promise<ArrayBuffer> {
  if (typeof file.arrayBuffer === 'function') return file.arrayBuffer()
  return new Promise<ArrayBuffer>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as ArrayBuffer)
    reader.onerror = () => reject(reader.error ?? new Error('the file could not be read'))
    reader.readAsArrayBuffer(file)
  })
}

async function run(
  file: File,
  limits: InspectionLimits,
  ensureTime: () => void,
): Promise<BundleInspection> {
  ensureTime()
  const JSZip = await loadJSZip()

  let archive: LoadedZip
  try {
    const bytes = await readBytes(file)
    // createFolders:false — the folder tree is derived from the paths below,
    // the same way validate.py sees it after extraction.
    archive = await new JSZip().loadAsync(bytes, { createFolders: false })
  } catch (cause) {
    if (cause instanceof InspectionError) throw cause
    throw new InspectionError(
      'That file could not be read as a zip archive. .rar, .7z and .tar.gz are not accepted; re-save it as a .zip.',
    )
  }

  const warnings: string[] = []
  const members: Member[] = []
  let counted = 0

  for (const raw of Object.values(archive.files)) {
    const name = normalise(raw.name)
    if (isIgnored(name)) continue

    counted += 1
    if (counted > limits.maxEntries) {
      throw new InspectionError(
        `The bundle has more than ${limits.maxEntries} entries, which the build rejects — this is almost always a stray renv/ library, .git/ or node_modules/ folder that should not be in the upload.`,
      )
    }

    checkPath(name)
    const dir = raw.dir || name.endsWith('/')
    members.push({
      name: dir && name.endsWith('/') ? name.slice(0, -1) : name,
      dir,
      entry: raw,
    })
  }

  if (members.filter((m) => !m.dir).length === 0) {
    throw new InspectionError('The archive contains no files.')
  }

  ensureTime()
  const { entrypoint, wrapper } = resolveEntrypoint(members)
  if (wrapper) {
    warnings.push(
      `Everything is inside one wrapper directory (${wrapper}/). The build flattens it, so ${entrypoint} ends up at the bundle root.`,
    )
  }

  const appRoot = wrapper ? `${wrapper}/` : ''
  const byName = new Map(members.filter((m) => !m.dir).map((m) => [m.name, m.entry]))

  const resolved = await resolvePackages(byName, appRoot, limits, warnings, ensureTime)

  if (resolved.packages.length === 0) {
    warnings.push(
      'No R packages were found in the bundle. Nothing is being guessed — type the list the app needs below.',
    )
  }

  return {
    entrypoint,
    wrapper,
    packages: resolved.packages,
    source: resolved.source,
    warnings,
  }
}

async function readTextEntry(
  entry: ZipEntry,
  label: string,
  limits: InspectionLimits,
): Promise<string> {
  const size = declaredSize(entry)
  if (size !== null && size > limits.maxTextBytes) {
    throw new InspectionError(
      `${label} is ${Math.round(size / 1024)} kB, too large to read here (cap ${Math.round(limits.maxTextBytes / 1024)} kB).`,
    )
  }
  return entry.async('string')
}

async function resolvePackages(
  byName: Map<string, ZipEntry>,
  appRoot: string,
  limits: InspectionLimits,
  warnings: string[],
  ensureTime: () => void,
): Promise<{ packages: string[]; source: PackageSource }> {
  // Precedence is the spec's: renv.lock, else packages.txt, else a scan.
  // A declared file that cannot be read does NOT silently vanish — it says
  // so and the next source is tried.
  const lock = byName.get(`${appRoot}renv.lock`)
  if (lock) {
    try {
      const packages = packagesFromRenvLock(await readTextEntry(lock, 'renv.lock', limits))
      if (packages.length > 0) return { packages, source: 'renv.lock' }
      warnings.push('renv.lock lists no packages; falling back to the other sources.')
    } catch (cause) {
      warnings.push(
        `${describe(cause, 'renv.lock could not be read.')} Falling back to the other sources.`,
      )
    }
  }

  const txt = byName.get(`${appRoot}packages.txt`)
  if (txt) {
    try {
      const packages = packagesFromPackagesTxt(
        await readTextEntry(txt, 'packages.txt', limits),
      )
      if (packages.length > 0) return { packages, source: 'packages.txt' }
      warnings.push('packages.txt is empty; falling back to a source scan.')
    } catch (cause) {
      warnings.push(
        `${describe(cause, 'packages.txt could not be read.')} Falling back to a source scan.`,
      )
    }
  }

  const packages = await scanSources(byName, appRoot, limits, warnings, ensureTime)
  return packages.length > 0
    ? { packages, source: 'scan' }
    : { packages: [], source: 'none' }
}

async function scanSources(
  byName: Map<string, ZipEntry>,
  appRoot: string,
  limits: InspectionLimits,
  warnings: string[],
  ensureTime: () => void,
): Promise<string[]> {
  const sources = [...byName.keys()]
    .filter((name) => name.startsWith(appRoot) && isSourceFile(name))
    .sort()

  if (sources.length === 0) return []

  const scanning = sources.slice(0, limits.maxScanFiles)
  if (sources.length > scanning.length) {
    warnings.push(
      `The bundle has ${sources.length} R source files; only the first ${limits.maxScanFiles} were scanned. Check the list carefully.`,
    )
  }

  const found: string[] = []
  let total = 0
  let skippedBig = 0

  for (const name of scanning) {
    ensureTime()
    const entry = byName.get(name)
    if (!entry) continue

    const size = declaredSize(entry)
    if (size !== null && size > limits.maxScanFileBytes) {
      skippedBig += 1
      continue
    }
    if (total >= limits.maxScanTotalBytes) {
      warnings.push(
        `Stopped scanning after ${Math.round(limits.maxScanTotalBytes / 1024)} kB of R source. Check the list carefully.`,
      )
      break
    }

    let text: string
    try {
      text = await entry.async('string')
    } catch {
      continue
    }
    total += size ?? text.length
    found.push(...packagesFromSource(text))
  }

  if (skippedBig > 0) {
    warnings.push(
      `${skippedBig} R file${skippedBig === 1 ? ' was' : 's were'} too large to scan and ${skippedBig === 1 ? 'was' : 'were'} skipped.`,
    )
  }

  // Traversal order is the archive's, so sort for a stable, readable list.
  return dedupe(found).sort((a, b) => a.localeCompare(b))
}

function describe(cause: unknown, fallback: string): string {
  return cause instanceof Error && cause.message ? cause.message : fallback
}

/** How the source reads in the UI. */
export function sourceLabel(source: PackageSource): string {
  switch (source) {
    case 'renv.lock':
      return 'renv.lock'
    case 'packages.txt':
      return 'packages.txt'
    case 'scan':
      return 'library() calls'
    case 'none':
      return 'nothing found'
  }
}
