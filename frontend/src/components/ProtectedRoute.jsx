import { useLocation, Navigate } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'
import { Loader2 } from 'lucide-react'
import { resolveLoginUrl } from '../utils/authRouting'

export default function ProtectedRoute({ children }) {
  const { isAuthenticated, isLoading } = useAuth()
  const location = useLocation()

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-950">
        <div className="text-center">
          <Loader2 className="w-8 h-8 animate-spin text-brand-600 mx-auto" />
          <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">Loading...</p>
        </div>
      </div>
    )
  }

  if (!isAuthenticated) {
    // Standalone (PLAT-353): land on mail's own login page, which offers
    // "Sign in with Shizuha ID" (OIDC). BrowserRouter applies VITE_BASE_PATH
    // to this router-relative route, so standalone /mail/ reaches
    // /mail/login without escaping to the Home SPA.
    const configuredLoginUrl = import.meta.env.VITE_LOGIN_URL
    if (!configuredLoginUrl) {
      return <Navigate to="/login" replace state={{ from: location }} />
    }

    // Integrated deployments may explicitly hand auth to another application.
    const loginUrl = resolveLoginUrl({
      basePath: import.meta.env.VITE_BASE_PATH,
      override: configuredLoginUrl,
    })
    const separator = loginUrl.includes('?') ? '&' : '?'
    const currentPath = window.location.pathname + window.location.search
    window.location.href = `${loginUrl}${separator}continue=${encodeURIComponent(currentPath)}`
    return null
  }

  return children
}
