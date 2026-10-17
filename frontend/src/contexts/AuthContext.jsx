import { createContext, useContext, useState, useEffect, useCallback } from 'react'
import api, { authApi } from '../services/api'

const AuthContext = createContext(null)

// Token storage keys - shared across all Shizuha apps
const ACCESS_TOKEN_KEY = 'shizuha_access_token'
const REFRESH_TOKEN_KEY = 'shizuha_refresh_token'
const USER_KEY = 'shizuha_user'

// "Sign in with Shizuha ID" (PLAT-353): the backend BFF callback delivers
// tokens in the URL fragment (never hits server logs). Capture + strip them
// synchronously on module load, before any auth check runs.
function captureOAuthFragment() {
  if (typeof window === 'undefined' || !window.location.hash) return
  const params = new URLSearchParams(window.location.hash.slice(1))
  const access = params.get('access')
  if (!access) return
  localStorage.setItem(ACCESS_TOKEN_KEY, access)
  const refresh = params.get('refresh')
  if (refresh) localStorage.setItem(REFRESH_TOKEN_KEY, refresh)
  // Strip tokens from the URL/history
  window.history.replaceState(null, '', window.location.pathname + window.location.search)
}
captureOAuthFragment()

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [isLoading, setIsLoading] = useState(true)

  // Try to refresh the access token using refresh token
  const refreshAccessToken = useCallback(async () => {
    const refreshToken = localStorage.getItem(REFRESH_TOKEN_KEY)
    if (!refreshToken) {
      return false
    }

    try {
      // Call shizuha-id's refresh endpoint
      const response = await fetch('/id/api/auth/refresh/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh: refreshToken }),
      })

      if (!response.ok) {
        return false
      }

      const data = await response.json()
      const newAccessToken = data.access

      // Save new tokens
      localStorage.setItem(ACCESS_TOKEN_KEY, newAccessToken)
      if (data.refresh) {
        localStorage.setItem(REFRESH_TOKEN_KEY, data.refresh)
      }

      return true
    } catch (error) {
      console.error('Token refresh failed:', error)
      return false
    }
  }, [])

  // Check authentication status
  const checkAuth = useCallback(async () => {
    const accessToken = localStorage.getItem(ACCESS_TOKEN_KEY)

    if (!accessToken) {
      // No token at all - redirect to login
      setUser(null)
      setIsLoading(false)
      return
    }

    try {
      const response = await authApi.getUser()
      setUser(response.data)
      localStorage.setItem(USER_KEY, JSON.stringify(response.data))
    } catch (error) {
      console.error('Auth check failed:', error)

      // If 401, try to refresh the token
      if (error.response?.status === 401) {
        const refreshed = await refreshAccessToken()
        if (refreshed) {
          // Retry with new token
          try {
            const response = await authApi.getUser()
            setUser(response.data)
            localStorage.setItem(USER_KEY, JSON.stringify(response.data))
            setIsLoading(false)
            return
          } catch (retryError) {
            console.error('Auth retry failed after refresh:', retryError)
          }
        }
      }

      // Clear auth state if all attempts failed
      setUser(null)
      localStorage.removeItem(ACCESS_TOKEN_KEY)
      localStorage.removeItem(REFRESH_TOKEN_KEY)
      localStorage.removeItem(USER_KEY)
    } finally {
      setIsLoading(false)
    }
  }, [refreshAccessToken])

  useEffect(() => {
    checkAuth()
  }, [checkAuth])

  // Listen for storage events (cross-tab sync)
  useEffect(() => {
    const handleStorageChange = (e) => {
      if (e.key === ACCESS_TOKEN_KEY || e.key === USER_KEY) {
        checkAuth()
      }
    }

    window.addEventListener('storage', handleStorageChange)
    return () => window.removeEventListener('storage', handleStorageChange)
  }, [checkAuth])

  const logout = () => {
    // Clear local state
    setUser(null)
    localStorage.removeItem(ACCESS_TOKEN_KEY)
    localStorage.removeItem(REFRESH_TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
    // Redirect to Shizuha ID logout
    window.location.href = '/id/logout'
  }

  return (
    <AuthContext.Provider value={{
      user,
      isAuthenticated: !!user,
      isLoading,
      logout,
      checkAuth,
    }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return context
}
