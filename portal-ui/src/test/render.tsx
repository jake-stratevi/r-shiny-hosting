import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { MemoryRouter } from 'react-router-dom'
import type { Me } from '../api/types'
import { MeContext } from '../lib/meContext'

export const adminMe: Me = { email: 'jake@stratevi.com', is_admin: true }
export const clientMe: Me = { email: 'reviewer@client-example.com', is_admin: false }

/** A page under the two providers App gives it: the router and `me`. */
export function renderPage(
  ui: ReactElement,
  { me = adminMe, route = '/' }: { me?: Me | null; route?: string } = {},
) {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <MeContext.Provider value={me}>{ui}</MeContext.Provider>
    </MemoryRouter>,
  )
}
