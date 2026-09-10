import { useEffect, useState } from 'react'

/**
 * Light / dark / system, the way assembled.work's `useAppearance` does it:
 * the preference is one of three values, the resolved theme is one of two,
 * and "system" keeps tracking the OS after the choice is made rather than
 * freezing whatever it was at the time.
 *
 * The theme is a `dark` class on <html>; every colour is a CSS variable
 * redefined under `.dark`, so nothing below this file has to know.
 */
export type Appearance = 'light' | 'dark' | 'system'
export type ResolvedAppearance = 'light' | 'dark'

export const APPEARANCE_KEY = 'appearance'
export const APPEARANCES: readonly Appearance[] = ['light', 'dark', 'system']

export function isAppearance(value: unknown): value is Appearance {
  return value === 'light' || value === 'dark' || value === 'system'
}

/**
 * jsdom has no `matchMedia`, and a browser with site data blocked throws on
 * `localStorage`. Both are read on every render path here, so both fail soft:
 * no media query means "not dark", no storage means "system".
 */
function darkQuery(): MediaQueryList | null {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return null
  try {
    return window.matchMedia('(prefers-color-scheme: dark)')
  } catch {
    return null
  }
}

export function prefersDark(): boolean {
  return darkQuery()?.matches ?? false
}

export function readAppearance(): Appearance {
  try {
    const stored = window.localStorage.getItem(APPEARANCE_KEY)
    return isAppearance(stored) ? stored : 'system'
  } catch {
    return 'system'
  }
}

export function resolveAppearance(value: Appearance): ResolvedAppearance {
  if (value === 'system') return prefersDark() ? 'dark' : 'light'
  return value
}

export function applyAppearance(value: Appearance): void {
  if (typeof document === 'undefined') return
  const root = document.documentElement
  root.classList.toggle('dark', resolveAppearance(value) === 'dark')
  root.dataset.appearance = value
}

/**
 * Called once at startup. index.html applies the stored theme before React
 * mounts (so there is no white flash on a dark machine); this re-applies it
 * and keeps "system" live for the rest of the session.
 */
export function initializeAppearance(): void {
  applyAppearance(readAppearance())

  const query = darkQuery()
  query?.addEventListener('change', () => {
    if (readAppearance() === 'system') applyAppearance('system')
  })
}

// A one-line store so the toggle in the sidebar footer and anything else that
// cares stay in step without a context provider around the whole app.
// localStorage IS the store — there is no cached copy to fall out of sync
// with it, or to leak between tests.
const listeners = new Set<() => void>()

export function setAppearance(value: Appearance): void {
  try {
    window.localStorage.setItem(APPEARANCE_KEY, value)
  } catch {
    // Private window, or site data blocked. The choice still applies for this
    // page view; it just will not be remembered.
  }
  applyAppearance(value)
  for (const listener of listeners) listener()
}

export function useAppearance(): {
  appearance: Appearance
  resolved: ResolvedAppearance
  setAppearance: (value: Appearance) => void
} {
  const [appearance, setLocal] = useState<Appearance>(readAppearance)

  useEffect(() => {
    const listener = () => setLocal(readAppearance())
    listeners.add(listener)
    listener()

    // While the preference is "system", an OS theme change has to re-render
    // the toggle too — `resolved` is part of what it reports.
    const query = darkQuery()
    query?.addEventListener('change', listener)

    return () => {
      listeners.delete(listener)
      query?.removeEventListener('change', listener)
    }
  }, [])

  return { appearance, resolved: resolveAppearance(appearance), setAppearance }
}
