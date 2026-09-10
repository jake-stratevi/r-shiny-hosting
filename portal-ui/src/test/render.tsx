import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { MemoryRouter } from 'react-router-dom'
import type { Me } from '../api/types'
import { MeContext } from '../lib/meContext'

export const adminMe: Me = {
  email: 'jake@stratevi.com',
  is_admin: true,
  can_create: true,
}

/** An admin who is not on `creator_emails`: no "+" affordance anywhere. */
export const adminNoCreateMe: Me = {
  email: 'nick@stratevi.com',
  is_admin: true,
  can_create: false,
}

/** A creator who is not an admin: the wizard, but no control plane. */
export const creatorMe: Me = {
  email: 'yi@stratevi.com',
  is_admin: false,
  can_create: true,
}

export const clientMe: Me = {
  email: 'reviewer@client-example.com',
  is_admin: false,
  can_create: false,
}

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
