import { describe, expect, it } from 'vitest'
import {
  InspectionError,
  inspectBundle,
  loadJSZip,
  packagesFromPackagesTxt,
  packagesFromRenvLock,
  packagesFromSource,
  sourceLabel,
} from './inspectBundle'

/**
 * Real zips, built with the same reader the wizard uses. A hand-rolled
 * fixture would only prove that the parser agrees with itself.
 */
async function makeZip(files: Record<string, string>, name = 'app.zip'): Promise<File> {
  const JSZip = await loadJSZip()
  const zip = new JSZip()
  for (const [path, content] of Object.entries(files)) zip.file(path, content)
  const bytes = await zip.generateAsync({ type: 'arraybuffer' })
  return new File([bytes], name, { type: 'application/zip' })
}

const renvLock = (...names: string[]) =>
  JSON.stringify({
    R: { Version: '4.3.1' },
    Packages: Object.fromEntries(
      names.map((n) => [n, { Package: n, Version: '1.0.0', Source: 'Repository' }]),
    ),
  })

async function rejection(promise: Promise<unknown>): Promise<InspectionError> {
  try {
    await promise
  } catch (err) {
    expect(err).toBeInstanceOf(InspectionError)
    return err as InspectionError
  }
  throw new Error('expected the inspection to be rejected')
}

// ---------------------------------------------------------------------------
// Entrypoint — this must agree with proxy-app/buildspec/validate.py's
// resolve_entrypoint(), or the wizard says "looks good" and the build says no.
// ---------------------------------------------------------------------------

describe('inspectBundle — entrypoint', () => {
  it('takes app.R at the bundle root', async () => {
    const found = await inspectBundle(
      await makeZip({ 'app.R': 'library(shiny)\nshinyApp(ui, server)\n' }),
    )
    expect(found.entrypoint).toBe('app.R')
    expect(found.wrapper).toBeNull()
  })

  it('takes ui.R + server.R at the bundle root', async () => {
    const found = await inspectBundle(
      await makeZip({ 'ui.R': 'library(shiny)', 'server.R': 'library(shiny)' }),
    )
    expect(found.entrypoint).toBe('ui.R+server.R')
  })

  it('prefers app.R over a ui.R/server.R pair, as validate.py does', async () => {
    const found = await inspectBundle(
      await makeZip({ 'app.R': 'x', 'ui.R': 'x', 'server.R': 'x' }),
    )
    expect(found.entrypoint).toBe('app.R')
  })

  it('flattens exactly one wrapper directory, and says so', async () => {
    const found = await inspectBundle(
      await makeZip({
        'my-app/app.R': 'library(shiny)',
        'my-app/renv.lock': renvLock('shiny'),
      }),
    )
    expect(found.entrypoint).toBe('app.R')
    expect(found.wrapper).toBe('my-app')
    expect(found.warnings.join(' ')).toMatch(/wrapper directory \(my-app\/\)/)
    // The wrapper is the app root: its renv.lock is the one that counts.
    expect(found.source).toBe('renv.lock')
    expect(found.packages).toEqual(['shiny'])
  })

  it('rejects two directories that both look like the app', async () => {
    const error = await rejection(
      inspectBundle(
        await makeZip({ 'one/app.R': 'x', 'two/ui.R': 'x', 'two/server.R': 'x' }),
      ),
    )
    expect(error.message).toMatch(/More than one directory looks like the app: one, two/)
  })

  it('rejects half a pair at the root, naming the missing file', async () => {
    const error = await rejection(
      inspectBundle(await makeZip({ 'ui.R': 'x', 'helpers.R': 'x' })),
    )
    expect(error.message).toMatch(/Found ui\.R at the bundle root but no server\.R/)
  })

  it('a dangling ui.R at the root wins over a valid wrapper, as validate.py does', async () => {
    // half_pair() is checked BEFORE the wrapper scan in validate.py, so this
    // bundle is rejected even though inner/ would otherwise be a candidate.
    const error = await rejection(
      inspectBundle(await makeZip({ 'ui.R': 'x', 'inner/app.R': 'x' })),
    )
    expect(error.message).toMatch(/no server\.R/)
  })

  it('rejects a bundle with no entrypoint anywhere, listing the top level', async () => {
    const error = await rejection(
      inspectBundle(await makeZip({ 'README.md': '#', 'data/values.csv': 'a,b' })),
    )
    expect(error.message).toMatch(/No app\.R or ui\.R\/server\.R found/)
    expect(error.message).toMatch(/README\.md, data/)
  })

  it('refuses a wrapper whose contents would collide at the root', async () => {
    const error = await rejection(
      inspectBundle(
        await makeZip({ 'README.md': 'root', 'my-app/app.R': 'x', 'my-app/README.md': 'inner' }),
      ),
    )
    expect(error.message).toMatch(/cannot be flattened: it contains README\.md/)
  })

  it('ignores __MACOSX/ and .DS_Store the way validate.py does', async () => {
    const found = await inspectBundle(
      await makeZip({
        '.DS_Store': 'junk',
        '__MACOSX/._app.R': 'junk',
        '__MACOSX/app.R': 'junk',
        'my-app/app.R': 'library(shiny)',
        'my-app/.DS_Store': 'junk',
      }),
    )
    // __MACOSX is not a second candidate directory, and .DS_Store is not
    // part of the top-level listing.
    expect(found.entrypoint).toBe('app.R')
    expect(found.wrapper).toBe('my-app')
  })

  it('treats a backslash as a separator, so a Windows zip still resolves', async () => {
    const found = await inspectBundle(await makeZip({ 'my-app\\app.R': 'library(shiny)' }))
    expect(found.entrypoint).toBe('app.R')
    expect(found.wrapper).toBe('my-app')
  })

  it('says plainly when the file is not a zip at all', async () => {
    const error = await rejection(
      inspectBundle(new File(['not a zip, just text'], 'app.zip')),
    )
    expect(error.message).toMatch(/could not be read as a zip archive/)
  })
})

// ---------------------------------------------------------------------------
// Packages — renv.lock, then packages.txt, then a scan.
// ---------------------------------------------------------------------------

describe('inspectBundle — package sources and their precedence', () => {
  it('reads renv.lock in preference to everything else', async () => {
    const found = await inspectBundle(
      await makeZip({
        'app.R': 'library(neverFromScan)',
        'renv.lock': renvLock('shiny', 'dplyr'),
        'packages.txt': 'notThisOne',
      }),
    )
    expect(found.source).toBe('renv.lock')
    expect(found.packages).toEqual(['shiny', 'dplyr'])
  })

  it('reads packages.txt when there is no renv.lock, ignoring blanks and comments', async () => {
    const found = await inspectBundle(
      await makeZip({
        'app.R': 'library(neverFromScan)',
        'packages.txt': '# what the app needs\nshiny\n\n  dplyr  \nggplot2 # plotting\n',
      }),
    )
    expect(found.source).toBe('packages.txt')
    expect(found.packages).toEqual(['shiny', 'dplyr', 'ggplot2'])
  })

  it('scans library()/require()/requireNamespace() when neither file exists', async () => {
    const found = await inspectBundle(
      await makeZip({
        'app.R': 'library(shiny)\nrequire("dplyr")\n',
        'helpers.R': "requireNamespace('jsonlite')\nlibrary(shiny)\n",
        'report.Rmd': '```{r}\nlibrary(knitr)\n```\n',
        'notes.txt': 'library(ignoreMe)',
      }),
    )
    expect(found.source).toBe('scan')
    expect(found.packages).toEqual(['dplyr', 'jsonlite', 'knitr', 'shiny'])
  })

  it('falls back with a warning when renv.lock is unreadable', async () => {
    const found = await inspectBundle(
      await makeZip({
        'app.R': 'x',
        'renv.lock': '{ this is not json',
        'packages.txt': 'shiny\n',
      }),
    )
    expect(found.source).toBe('packages.txt')
    expect(found.warnings.join(' ')).toMatch(/renv\.lock is not valid JSON/)
  })

  it('falls back when renv.lock has no Packages section', async () => {
    const found = await inspectBundle(
      await makeZip({ 'app.R': 'library(shiny)', 'renv.lock': '{"R":{"Version":"4.3.1"}}' }),
    )
    expect(found.source).toBe('scan')
    expect(found.packages).toEqual(['shiny'])
    expect(found.warnings.join(' ')).toMatch(/no "Packages" section/)
  })

  it('warns rather than guesses when nothing declares a package', async () => {
    const found = await inspectBundle(await makeZip({ 'app.R': 'shinyApp(ui, server)\n' }))
    expect(found.source).toBe('none')
    expect(found.packages).toEqual([])
    expect(found.warnings.join(' ')).toMatch(/No R packages were found/)
  })
})

describe('package parsers', () => {
  it('takes the Packages keys out of an renv.lock, deduped and in order', () => {
    expect(packagesFromRenvLock(renvLock('shiny', 'DT'))).toEqual(['shiny', 'DT'])
  })

  it('rejects an renv.lock with no Packages object', () => {
    expect(() => packagesFromRenvLock('{"Packages": []}')).toThrow(InspectionError)
  })

  it('drops blanks, full-line comments and trailing comments from packages.txt', () => {
    expect(packagesFromPackagesTxt('#head\n\nshiny\r\nDT  # tables\nshiny\n')).toEqual([
      'shiny',
      'DT',
    ])
  })

  it('finds quoted and unquoted calls but not lookalike identifiers', () => {
    const text = [
      'library(shiny)',
      "library('dplyr')",
      'require("tidyr")',
      "requireNamespace('jsonlite', quietly = TRUE)",
      'mylibrary(nope)',
      'x <- librarian(nope2)',
    ].join('\n')
    expect(packagesFromSource(text).sort()).toEqual([
      'dplyr',
      'jsonlite',
      'shiny',
      'tidyr',
    ])
  })

  it('names the source in words for the UI', () => {
    expect(sourceLabel('scan')).toBe('library() calls')
    expect(sourceLabel('renv.lock')).toBe('renv.lock')
  })
})

// ---------------------------------------------------------------------------
// Caps — a huge or hostile zip must produce a message, never a frozen tab.
// ---------------------------------------------------------------------------

describe('inspectBundle — work caps', () => {
  it('bails out on an archive with more entries than the cap', async () => {
    const files: Record<string, string> = { 'app.R': 'library(shiny)' }
    for (let i = 0; i < 8; i += 1) files[`data/f${i}.csv`] = 'a,b'
    const error = await rejection(
      inspectBundle(await makeZip(files), { limits: { maxEntries: 4 } }),
    )
    expect(error.message).toMatch(/more than 4 entries/)
  })

  it('bails out when the deadline passes, without hanging', async () => {
    const error = await rejection(
      inspectBundle(await makeZip({ 'app.R': 'library(shiny)' }), {
        limits: { timeoutMs: 0 },
      }),
    )
    expect(error.message).toMatch(/took too long/)
  })

  it('scans at most maxScanFiles source files, and says it stopped', async () => {
    const found = await inspectBundle(
      await makeZip({
        'app.R': 'library(shiny)',
        'b.R': 'library(bbb)',
        'c.R': 'library(ccc)',
      }),
      { limits: { maxScanFiles: 1 } },
    )
    // Sorted traversal: only app.R is read.
    expect(found.packages).toEqual(['shiny'])
    expect(found.warnings.join(' ')).toMatch(/only the first 1 were scanned/)
  })

  it('skips a source file bigger than the per-file cap', async () => {
    const found = await inspectBundle(
      await makeZip({
        'app.R': `library(shiny)\n# ${'x'.repeat(4000)}\n`,
        'small.R': 'library(dplyr)',
      }),
      { limits: { maxScanFileBytes: 1000 } },
    )
    expect(found.packages).toEqual(['dplyr'])
    expect(found.warnings.join(' ')).toMatch(/too large to scan/)
  })

  it('does not decompress an renv.lock bigger than the text cap', async () => {
    const found = await inspectBundle(
      await makeZip({
        'app.R': 'x',
        'renv.lock': renvLock(...Array.from({ length: 200 }, (_, i) => `pkg${i}`)),
        'packages.txt': 'shiny\n',
      }),
      { limits: { maxTextBytes: 512 } },
    )
    expect(found.source).toBe('packages.txt')
    expect(found.warnings.join(' ')).toMatch(/renv\.lock is \d+ kB, too large to read here/)
  })
})
