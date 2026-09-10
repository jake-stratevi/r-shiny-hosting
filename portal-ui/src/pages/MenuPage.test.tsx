import { screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { MenuApp } from '../api/types'
import { WAKE_HINT } from '../lib/appDisplay'
import { clientMe, renderPage } from '../test/render'
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
  },
  {
    host: 'payer-survey.tools.stratevi.com',
    label: 'Payer Survey Explorer',
    description: 'Cross-tabs of the Q2 payer survey.',
    url: 'https://payer-survey.tools.stratevi.com',
    live_state: 'asleep',
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

  it('shows admins a "New app" card marked as P2, and never wires it up', async () => {
    renderPage(<MenuPage />)

    const card = await screen.findByText('New app')
    expect(screen.getByText('Coming in P2')).toBeInTheDocument()
    // Deliberately inert: no route behind it until the wizard exists.
    expect(card.closest('a')).toBeNull()
    expect(card.closest('[aria-disabled="true"]')).not.toBeNull()
  })

  it('hides the "New app" card from non-admins', async () => {
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
