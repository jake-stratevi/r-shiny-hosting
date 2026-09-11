import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { renderPage } from '../test/render'
import { HelpPage } from './HelpPage'

/**
 * The page is static prose, so there is no behaviour to test — what these
 * assertions protect is the two things about it that can silently go wrong.
 *
 * 1. Every question is on screen at once. There is no accordion, and a later
 *    "tidy-up" that hid the answers behind a click would break the whole
 *    point of the page: it is readable in one pass and searchable with the
 *    browser's own find.
 * 2. The release answer stays honest. There is no self-service release path
 *    (ROADMAP, P2b), and the day someone softens that sentence into "use the
 *    update button" is the day this page starts costing people an afternoon.
 */
describe('the Help page', () => {
  it('is titled Help and says what it is', () => {
    renderPage(<HelpPage />, { route: '/help' })

    expect(screen.getByRole('heading', { name: 'Help', level: 1 })).toBeInTheDocument()
    expect(
      screen.getByText(/How this platform works, and what to check/),
    ).toBeInTheDocument()
  })

  it('shows every question and answer expanded, with nothing to click', () => {
    renderPage(<HelpPage />, { route: '/help' })

    const questions = screen.getAllByRole('heading', { level: 2 })
    expect(questions.length).toBeGreaterThanOrEqual(9)
    for (const question of questions) {
      expect(question.textContent?.trim()).toMatch(/\?$/)
    }

    // No accordion: the page offers no controls of its own at all.
    expect(screen.queryAllByRole('button')).toHaveLength(0)
  })

  it('covers the questions the platform actually generates', () => {
    renderPage(<HelpPage />, { route: '/help' })

    for (const question of [
      /What can I upload\?/,
      /What happens after I upload\?/,
      /take 30–60 seconds to open/,
      /When does an app go to sleep\?/,
      /Who can see my app\?/,
      /What happens when an app expires\?/,
      /How do I update an app’s code\?/,
      /what do I check\?/,
      /What does my R code need to do differently/,
    ]) {
      expect(screen.getByRole('heading', { name: question, level: 2 })).toBeInTheDocument()
    }
  })

  it('names the log group and stream shape, which is the answer people need', () => {
    renderPage(<HelpPage />, { route: '/help' })

    expect(screen.getByText('/ecs/shiny/apps')).toBeInTheDocument()
    expect(screen.getByText('<app-key>/app/<task-id>')).toBeInTheDocument()
  })

  it('is honest that there is no self-service release path', () => {
    renderPage(<HelpPage />, { route: '/help' })

    const answer = screen
      .getByRole('heading', { name: /How do I update an app’s code\?/ })
      .parentElement?.textContent
    expect(answer).toMatch(/You can’t/)
    expect(answer).toMatch(/by hand/)
  })

  it('warns that the session cap ends a run in progress', () => {
    renderPage(<HelpPage />, { route: '/help' })

    const answer = screen
      .getByRole('heading', { name: 'When does an app go to sleep?' })
      .parentElement?.textContent
    expect(answer).toMatch(/while you are\s+using it/)
  })

  it('says entitlement is the control and the random hostname is not', () => {
    renderPage(<HelpPage />, { route: '/help' })

    const answer = screen
      .getByRole('heading', { name: 'Who can see my app?' })
      .parentElement?.textContent
    expect(answer).toMatch(/Entitlement is the control/)
  })
})
