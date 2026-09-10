import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { MenuApp } from '../api/types'
import { WAKE_HINT } from '../lib/appDisplay'
import { adminNoCreateMe, clientMe, renderPage } from '../test/render'
import { MenuPage } from './MenuPage'

const menu = vi.hoisted(() => vi.fn())
vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return { ...actual, api: { ...actual.api, menu } }
})

const tiles: MenuApp[] = [
  {
    host: 'dashboard.tools.stratevi.com',
    label: 'Treatment Pathway Dashboard',
    description: 'Sankey of treatment sequences.',
    url: 'https://dashboard.tools.stratevi.com',
    live_state: 'awake',
    // Far enough out to stay "ok" rather than "soon", so the tone assertion
    // below does not start failing a week before whenever this is read.
    expires_at: Math.floor(Date.now() / 1000) + 90 * 86_400,
    last_active: Math.floor(Date.now() / 1000) - 3_600,
  },
  {
    host: 'payer-survey.tools.stratevi.com',
    label: 'Payer Survey Explorer',
    description: 'Cross-tabs of the Q2 payer survey.',
    url: 'https://payer-survey.tools.stratevi.com',
    live_state: 'asleep',
    expires_at: null,
    last_active: null,
  },
]

beforeEach(() => {
  menu.mockReset()
  menu.mockResolvedValue(tiles)
})

describe('MenuPage', () => {
  it('greets the signed-in person by name and shows their address', async () => {
    renderPage(<MenuPage />)

    const heading = await screen.findByRole('heading', { level: 1 })
    expect(heading).toHaveTextContent(/Good (morning|afternoon|evening), Jake/)
    expect(screen.getByText('Signed in as jake@stratevi.com')).toBeInTheDocument()
  })

  it('summarises how many tools are awake', async () => {
    renderPage(<MenuPage />)
    expect(
      await screen.findByText('1 of your 2 tools is awake right now.'),
    ).toBeInTheDocument()
  })

  it('renders a card grid whose cards link to the app', async () => {
    renderPage(<MenuPage />)

    const link = await screen.findByRole('link', { name: /Treatment Pathway Dashboard/ })
    expect(link).toHaveAttribute('href', 'https://dashboard.tools.stratevi.com')
    expect(screen.getAllByRole('listitem').length).toBeGreaterThanOrEqual(2)
  })

  it('warns that a sleeping tool takes a moment to start', async () => {
    renderPage(<MenuPage />)

    expect(await screen.findAllByText(WAKE_HINT)).toHaveLength(1)
    expect(screen.getByText('Asleep')).toBeInTheDocument()
    expect(screen.getByText('Awake')).toBeInTheDocument()
  })

  it('puts the link field on every card, with Copy and Open of its own', async () => {
    renderPage(<MenuPage />)

    await screen.findByRole('link', { name: /Treatment Pathway Dashboard/ })

    // The host, in the mono chip — not the full URL.
    expect(screen.getByText('dashboard.tools.stratevi.com')).toBeInTheDocument()
    expect(screen.getByText('payer-survey.tools.stratevi.com')).toBeInTheDocument()

    expect(
      screen.getByRole('button', { name: 'Copy link to dashboard.tools.stratevi.com' }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('link', { name: 'Open dashboard.tools.stratevi.com' }),
    ).toHaveAttribute('href', 'https://dashboard.tools.stratevi.com')
  })

  it('copies the whole URL rather than following the card link', async () => {
    // userEvent installs its own clipboard stub, which is the one the button
    // writes to; reading it back is the honest assertion.
    const user = userEvent.setup()
    renderPage(<MenuPage />)

    await user.click(
      await screen.findByRole('button', {
        name: 'Copy link to dashboard.tools.stratevi.com',
      }),
    )

    expect(await navigator.clipboard.readText()).toBe(
      'https://dashboard.tools.stratevi.com',
    )
    // The card's own link was not followed: the page is still the menu.
    expect(
      await screen.findByRole('button', { name: 'Link copied' }),
    ).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1 })).toBeInTheDocument()
  })

  it('says how ready each tool is as a metadata row with its own icon', async () => {
    renderPage(<MenuPage />)

    // Every card gets exactly one readiness row, and it is never empty: the
    // menu contract carries no expiry or last-active, so those rows are not
    // rendered at all rather than rendered as "—".
    for (const text of ['Ready now', WAKE_HINT]) {
      const row = await screen.findByText(text)
      expect(row.parentElement?.querySelector('svg')).toBeTruthy()
    }
  })

  it('gives a creator a "New app" card that opens the wizard', async () => {
    renderPage(<MenuPage />)

    const card = await screen.findByText('New app')
    expect(card.closest('a')).toHaveAttribute('href', '/admin/apps/new')
  })

  it('hides the "New app" card from an admin who may not create', async () => {
    renderPage(<MenuPage />, { me: adminNoCreateMe })

    await screen.findByRole('heading', { level: 1 })
    expect(screen.queryByText('New app')).not.toBeInTheDocument()
  })

  it('hides the "New app" card from a plain client', async () => {
    renderPage(<MenuPage />, { me: clientMe })

    await screen.findByRole('heading', { level: 1 })
    expect(screen.queryByText('New app')).not.toBeInTheDocument()
  })

  it('offers a way forward when nothing is shared', async () => {
    menu.mockResolvedValue([])
    renderPage(<MenuPage />, { me: clientMe })

    await waitFor(() =>
      expect(screen.getByText('Nothing is shared with you yet')).toBeInTheDocument(),
    )
    expect(screen.getByRole('link', { name: 'support@stratevi.com' })).toBeInTheDocument()
  })
})
