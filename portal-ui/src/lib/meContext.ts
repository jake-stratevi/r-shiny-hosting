import { createContext, useContext } from 'react'
import type { Me } from '../api/types'

/** null while /api/v1/me is still loading or if it failed. */
export const MeContext = createContext<Me | null>(null)

export function useMe(): Me | null {
  return useContext(MeContext)
}
