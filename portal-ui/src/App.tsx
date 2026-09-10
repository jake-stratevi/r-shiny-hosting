import type { ReactNode } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { ApiError, api } from './api/client'
import { Layout } from './components/Layout'
import { ErrorState, Loading, NoAdminAccess } from './components/states'
import { useResource } from './hooks/useResource'
import { MeContext, useMe } from './lib/meContext'
import { AdminAppDetailPage } from './pages/AdminAppDetailPage'
import { AdminAppsPage } from './pages/AdminAppsPage'
import { MenuPage } from './pages/MenuPage'

/** Admin routes are hidden in the nav and gated here; the API gates too. */
function RequireAdmin({ children }: { children: ReactNode }) {
  const me = useMe()
  if (!me) return <Loading label="Checking access" />
  if (!me.is_admin) return <NoAdminAccess />
  return <>{children}</>
}

function FullPage({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-canvas px-6">
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
            path="/admin"
            element={
              <RequireAdmin>
                <AdminAppsPage />
              </RequireAdmin>
            }
          />
          <Route
            path="/admin/apps/:host"
            element={
              <RequireAdmin>
                <AdminAppDetailPage />
              </RequireAdmin>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Layout>
    </MeContext.Provider>
  )
}
