import type { ReactNode } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { ApiError, api } from './api/client'
import { Layout } from './components/Layout'
import {
  ErrorState,
  Loading,
  NoAdminAccess,
  NoCreateAccess,
  NoStaffAccess,
} from './components/states'
import { useResource } from './hooks/useResource'
import { MeContext, useMe } from './lib/meContext'
import { AdminAppDetailPage } from './pages/AdminAppDetailPage'
import { AdminAppsPage } from './pages/AdminAppsPage'
import { CostsPage } from './pages/CostsPage'
import { BuildPage } from './pages/BuildPage'
import { HelpPage } from './pages/HelpPage'
import { MenuPage } from './pages/MenuPage'
import { NewAppWizard } from './pages/NewAppWizard'

/** Admin routes are hidden in the nav and gated here; the API gates too. */
function RequireAdmin({ children }: { children: ReactNode }) {
  const me = useMe()
  if (!me) return <Loading label="Checking access" />
  if (!me.is_admin) return <NoAdminAccess />
  return <>{children}</>
}

/**
 * Everything except the app menu belongs to the people who run the platform.
 * `is_staff` is the proxy's verdict on the signed-in domain, and an absent
 * field fails closed exactly as `can_create` does — a backend that predates
 * the field shuts these screens rather than opening them.
 *
 * This is the OUTER gate, so it wraps `RequireAdmin` and `RequireCreate`
 * rather than replacing either: a Stratevi account still needs the admin or
 * creator permission underneath, and an external client never reaches the
 * question. The API refuses all of this too; two gates is the point.
 */
function RequireStaff({ children }: { children: ReactNode }) {
  const me = useMe()
  if (!me) return <Loading label="Checking access" />
  if (!me.is_staff) return <NoStaffAccess />
  return <>{children}</>
}

/**
 * Creation is its own permission (portal-p2a.md "Decisions"): admin does NOT
 * imply it, and it does not imply admin. So the wizard and the build screen
 * gate on `can_create` alone, and an absent field fails closed.
 */
function RequireCreate({ children }: { children: ReactNode }) {
  const me = useMe()
  if (!me) return <Loading label="Checking access" />
  if (!me.can_create) return <NoCreateAccess />
  return <>{children}</>
}

function FullPage({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-6">
      <div className="w-full max-w-lg">{children}</div>
    </div>
  )
}

export default function App() {
  const { data: me, error, loading, reload } = useResource('me', (signal) => api.me(signal))

  if (loading) {
    return (
      <FullPage>
        <Loading />
      </FullPage>
    )
  }

  // /api/v1/me failing means the service is unreachable or the ALB session is
  // gone; there is nothing useful to render underneath it.
  if (error || !me) {
    return (
      <FullPage>
        <ErrorState
          error={error ?? new ApiError(0, 'The portal service did not return your identity.')}
          onRetry={() => reload()}
        />
      </FullPage>
    )
  }

  return (
    <MeContext.Provider value={me}>
      <Layout>
        <Routes>
          <Route path="/" element={<MenuPage />} />
          <Route
            path="/help"
            element={
              <RequireStaff>
                <HelpPage />
              </RequireStaff>
            }
          />
          <Route
            path="/admin"
            element={
              <RequireStaff>
                <RequireAdmin>
                  <AdminAppsPage />
                </RequireAdmin>
              </RequireStaff>
            }
          />
          {/* Before /admin/apps/:host: "costs" is a screen, not a hostname. */}
          <Route
            path="/admin/costs"
            element={
              <RequireStaff>
                <RequireAdmin>
                  <CostsPage />
                </RequireAdmin>
              </RequireStaff>
            }
          />
          {/* Before /admin/apps/:host, or "new" is read as a hostname. */}
          <Route
            path="/admin/apps/new"
            element={
              <RequireStaff>
                <RequireCreate>
                  <NewAppWizard />
                </RequireCreate>
              </RequireStaff>
            }
          />
          <Route
            path="/admin/apps/:host/build"
            element={
              <RequireStaff>
                <RequireCreate>
                  <BuildPage />
                </RequireCreate>
              </RequireStaff>
            }
          />
          <Route
            path="/admin/apps/:host"
            element={
              <RequireStaff>
                <RequireAdmin>
                  <AdminAppDetailPage />
                </RequireAdmin>
              </RequireStaff>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Layout>
    </MeContext.Provider>
  )
}
