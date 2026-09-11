import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { adminMe, renderPage } from '../test/render'
import { Layout } from './Layout'
import { Tour } from './Tour'

/**
 * Tour resolves its targets from two fixed DOM shapes: the rail's
 * `<nav aria-label="Main">` landmark (matched by each link's accessible
 * name — the same names the real `AppSidebar` renders, and the same ones
 * `Layout.test.tsx` already keys its own assertions on) and the dashboard's
 * `<main><ul className="grid">`. These unit tests build both by hand rather
 * than rendering the real `AppSidebar`, which is owned by a different agent
 * and has its own permission model in flux in this same working tree — a
 * hand-built rail keeps what each test exercises pinned down regardless of
 * that churn.
 */
function renderTour({
  navLinks = [],
  grid = false,
  route = '/',
}: {
  navLinks?: string[]
  grid?: boolean
  route?: string
} = {}) {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <nav aria-label="Main">
        <ul>
          {navLinks.map((name) => (
            <li key={name}>
              <a href="#">{name}</a>
            </li>
          ))}
        </ul>
      </nav>
      <main>
        {grid ? (
          <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <li>a card</li>
          </ul>
        ) : null}
        <Tour />
      </main>
    </MemoryRouter>,
  )
}

const dialog = () => screen.getByRole('dialog')
const tourButton = () => screen.getByRole('button', { name: 'Tour' })
const stepTitle = () => within(dialog()).getByRole('heading', { level: 2 }).textContent
const counter = () => within(dialog()).getByText(/^\d+ of \d+$/).textContent

describe('resolving steps against the real DOM', () => {
  it('shows only the step whose target exists when nothing else does', async () => {
    const user = userEvent.setup()
    renderTour({ navLinks: ['Apps'] })

    await user.click(tourButton())
    expect(counter()).toBe('1 of 1')
    expect(stepTitle()).toBe('Apps')
    // The only step, so its action already reads "Done".
    expect(within(dialog()).getByRole('button', { name: 'Done' })).toBeInTheDocument()
  })

  it('skips nav links that are not in the DOM, and does not leave a gap in the count', async () => {
    const user = userEvent.setup()
    // "Manage apps", "Costs" and "Help" are absent — as they are for anyone
    // without the matching permission — leaving exactly two resolvable steps.
    renderTour({ navLinks: ['Apps', 'New app'] })

    await user.click(tourButton())
    expect(counter()).toBe('1 of 2')
    expect(stepTitle()).toBe('Apps')

    await user.click(within(dialog()).getByRole('button', { name: 'Next' }))
    expect(counter()).toBe('2 of 2')
    expect(stepTitle()).toBe('Create an app')
    expect(within(dialog()).getByRole('button', { name: 'Done' })).toBeInTheDocument()
  })

  it('includes every nav step, in order, when every link is present', async () => {
    const user = userEvent.setup()
    renderTour({ navLinks: ['Apps', 'New app', 'Manage apps', 'Costs', 'Help'] })

    await user.click(tourButton())
    const titles = ['Apps', 'Create an app', 'Manage apps', 'Costs', 'Help']
    for (const title of titles) {
      expect(stepTitle()).toBe(title)
      const next = within(dialog()).queryByRole('button', { name: 'Next' })
      if (next) {
        // eslint-disable-next-line no-await-in-loop
        await user.click(next)
      }
    }
    expect(within(dialog()).getByRole('button', { name: 'Done' })).toBeInTheDocument()
  })

  it('includes the dashboard-cards step only on "/" and only when the grid is on the page', async () => {
    const user = userEvent.setup()
    renderTour({ navLinks: ['Apps'], grid: true, route: '/' })

    await user.click(tourButton())
    expect(counter()).toBe('1 of 2')
    await user.click(within(dialog()).getByRole('button', { name: 'Next' }))
    expect(stepTitle()).toBe('Your tools')
  })

  it('leaves the dashboard-cards step out on a route where an identical grid means something else', async () => {
    const user = userEvent.setup()
    // Same grid markup as MenuPage, but AdminAppsPage renders one just like
    // it at "/admin" for a different collection — the route is what tells
    // them apart, so off "/" the step must not appear even though the
    // selector would otherwise match.
    renderTour({ navLinks: ['Apps'], grid: true, route: '/admin' })

    await user.click(tourButton())
    expect(counter()).toBe('1 of 1')
    expect(stepTitle()).toBe('Apps')
  })

  it('falls back to one step anchored on the Tour button when nothing resolves', async () => {
    const user = userEvent.setup()
    // No "Main" nav rendered at all — e.g. a mobile drawer closed on a route
    // with no card grid of its own.
    render(
      <MemoryRouter initialEntries={['/admin/costs']}>
        <main>
          <Tour />
        </main>
      </MemoryRouter>,
    )

    await user.click(tourButton())
    expect(counter()).toBe('1 of 1')
    expect(stepTitle()).toBe('Tour')
    expect(screen.getByText(/nothing to point at/i)).toBeInTheDocument()
  })
})

describe('the Tour button, wired into the real header', () => {
  it('sits in the header and opens the tour on click', async () => {
    const user = userEvent.setup()
    renderPage(
      <Layout>
        <p>page body</p>
      </Layout>,
      { me: adminMe },
    )

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(tourButton().closest('header')).not.toBeNull()

    await user.click(tourButton())
    expect(dialog()).toBeInTheDocument()
  })

  it('moves forward and back through steps with Next and Back', async () => {
    const user = userEvent.setup()
    renderPage(
      <Layout>
        <p>page body</p>
      </Layout>,
      { me: adminMe },
    )

    await user.click(tourButton())
    expect(stepTitle()).toBe('Apps')
    expect(within(dialog()).queryByRole('button', { name: 'Back' })).not.toBeInTheDocument()

    await user.click(within(dialog()).getByRole('button', { name: 'Next' }))
    const second = stepTitle()
    expect(second).not.toBe('Apps')

    await user.click(within(dialog()).getByRole('button', { name: 'Back' }))
    expect(stepTitle()).toBe('Apps')
  })

  it('closes on Escape and returns focus to the Tour button', async () => {
    const user = userEvent.setup()
    renderPage(
      <Layout>
        <p>page body</p>
      </Layout>,
      { me: adminMe },
    )

    await user.click(tourButton())
    expect(dialog()).toBeInTheDocument()

    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(tourButton()).toHaveFocus()
  })

  it('closes on a scrim click and returns focus to the Tour button', async () => {
    const user = userEvent.setup()
    renderPage(
      <Layout>
        <p>page body</p>
      </Layout>,
      { me: adminMe },
    )

    await user.click(tourButton())
    await user.click(screen.getByTestId('tour-scrim'))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(tourButton()).toHaveFocus()
  })

  it('focuses the dialog when it opens', async () => {
    const user = userEvent.setup()
    renderPage(
      <Layout>
        <p>page body</p>
      </Layout>,
      { me: adminMe },
    )

    await user.click(tourButton())
    expect(dialog()).toHaveFocus()
  })

  it('keeps Tab cycling inside the dialog rather than escaping to the page', async () => {
    const user = userEvent.setup()
    renderPage(
      <Layout>
        <p>page body</p>
      </Layout>,
      { me: adminMe },
    )

    await user.click(tourButton())
    const closeButton = within(dialog()).getByRole('button', { name: 'Close tour' })
    const nextButton = within(dialog()).getByRole('button', { name: 'Next' })

    // Shift+Tab from the first focusable (Close) wraps to the last (Next).
    closeButton.focus()
    await user.tab({ shift: true })
    expect(nextButton).toHaveFocus()

    // Tab from the last focusable wraps back to the first.
    await user.tab()
    expect(closeButton).toHaveFocus()
  })
})
