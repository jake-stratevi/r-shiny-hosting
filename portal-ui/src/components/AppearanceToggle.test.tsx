import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { APPEARANCE_KEY, initializeAppearance, readAppearance } from '../lib/appearance'
import { AppearanceToggle } from './AppearanceToggle'

/**
 * jsdom ships no `matchMedia`, so every test that cares about "system" has
 * to say what the machine prefers. The stub is a real MediaQueryList enough
 * for the code under test: `matches`, and add/remove listener.
 */
function stubPrefersDark(dark: boolean) {
  const listeners = new Set<(event: MediaQueryListEvent) => void>()

  const query = {
    matches: dark,
    media: '(prefers-color-scheme: dark)',
    addEventListener: (_: string, fn: (event: MediaQueryListEvent) => void) =>
      listeners.add(fn),
    removeEventListener: (_: string, fn: (event: MediaQueryListEvent) => void) =>
      listeners.delete(fn),
  }

  vi.stubGlobal(
    'matchMedia',
    vi.fn(() => query),
  )

  /** Pretend the OS flipped: update `matches` and fire the listeners. */
  return function flip(nowDark: boolean) {
    query.matches = nowDark
    for (const fn of listeners) fn({ matches: nowDark } as MediaQueryListEvent)
  }
}

const isDark = () => document.documentElement.classList.contains('dark')

beforeEach(() => {
  localStorage.clear()
  document.documentElement.className = ''
  delete document.documentElement.dataset.appearance
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('AppearanceToggle', () => {
  it('offers exactly the three states, with system selected by default', () => {
    stubPrefersDark(false)
    render(<AppearanceToggle />)

    expect(screen.getByRole('group', { name: 'Color theme' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Light' })).toHaveAttribute(
      'aria-pressed',
      'false',
    )
    expect(screen.getByRole('button', { name: 'Dark' })).toHaveAttribute(
      'aria-pressed',
      'false',
    )
    expect(screen.getByRole('button', { name: 'Match system' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
  })

  it('puts the app in dark and remembers the choice', async () => {
    stubPrefersDark(false)
    const user = userEvent.setup()
    render(<AppearanceToggle />)

    await user.click(screen.getByRole('button', { name: 'Dark' }))

    expect(isDark()).toBe(true)
    expect(localStorage.getItem(APPEARANCE_KEY)).toBe('dark')
    expect(screen.getByRole('button', { name: 'Dark' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
  })

  it('puts the app in light even on a machine that prefers dark', async () => {
    stubPrefersDark(true)
    const user = userEvent.setup()
    render(<AppearanceToggle />)

    await user.click(screen.getByRole('button', { name: 'Light' }))

    expect(isDark()).toBe(false)
    expect(localStorage.getItem(APPEARANCE_KEY)).toBe('light')
  })

  it('hands the decision back to the machine on "Match system"', async () => {
    stubPrefersDark(true)
    const user = userEvent.setup()
    render(<AppearanceToggle />)

    await user.click(screen.getByRole('button', { name: 'Light' }))
    expect(isDark()).toBe(false)

    await user.click(screen.getByRole('button', { name: 'Match system' }))

    expect(isDark()).toBe(true)
    expect(localStorage.getItem(APPEARANCE_KEY)).toBe('system')
  })

  it('reads the stored choice back on the next visit', () => {
    stubPrefersDark(false)
    localStorage.setItem(APPEARANCE_KEY, 'dark')

    render(<AppearanceToggle />)

    expect(screen.getByRole('button', { name: 'Dark' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(readAppearance()).toBe('dark')
  })

  it('ignores a stored value that is not one of the three', () => {
    stubPrefersDark(false)
    localStorage.setItem(APPEARANCE_KEY, 'solarized')

    render(<AppearanceToggle />)

    expect(screen.getByRole('button', { name: 'Match system' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
  })
})

describe('initializeAppearance', () => {
  it('applies the stored theme before anything renders', () => {
    stubPrefersDark(false)
    localStorage.setItem(APPEARANCE_KEY, 'dark')

    initializeAppearance()

    expect(isDark()).toBe(true)
  })

  it('follows the OS while the preference is "system"', () => {
    const flip = stubPrefersDark(false)
    initializeAppearance()
    expect(isDark()).toBe(false)

    flip(true)

    expect(isDark()).toBe(true)
  })

  it('does not follow the OS once a theme has been pinned', () => {
    const flip = stubPrefersDark(false)
    localStorage.setItem(APPEARANCE_KEY, 'light')
    initializeAppearance()

    flip(true)

    expect(isDark()).toBe(false)
  })
})
