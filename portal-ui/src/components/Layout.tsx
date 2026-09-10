import type { ReactNode } from 'react'
import { NavLink } from 'react-router-dom'
import { useMe } from '../lib/meContext'

function navClass({ isActive }: { isActive: boolean }): string {
  return [
    'rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
    isActive ? 'bg-accent-soft text-accent' : 'text-muted hover:bg-canvas hover:text-ink',
  ].join(' ')
}

export function Layout({ children }: { children: ReactNode }) {
  const me = useMe()

  return (
    <div className="flex min-h-screen flex-col bg-canvas">
      <header className="border-b border-line bg-surface">
        <div className="mx-auto flex h-14 w-full max-w-6xl items-center gap-6 px-6">
          <NavLink to="/" className="text-[15px] font-semibold tracking-tight text-ink">
            Stratevi
            <span className="ml-2 font-normal text-faint">Tools</span>
          </NavLink>

          <nav className="flex items-center gap-1">
            <NavLink to="/" end className={navClass}>
              Your tools
            </NavLink>
            {/* Hidden for non-admins; the API enforces it regardless. */}
            {me?.is_admin ? (
              <NavLink to="/admin" className={navClass}>
                Admin
              </NavLink>
            ) : null}
          </nav>

          <span className="ml-auto hidden text-sm text-faint sm:block">{me?.email}</span>
        </div>
      </header>

      <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-10">{children}</main>

      <footer className="border-t border-line-soft">
        <div className="mx-auto w-full max-w-6xl px-6 py-5 text-xs text-faint">
          Stratevi · hosted analytics tools
        </div>
      </footer>
    </div>
  )
}
