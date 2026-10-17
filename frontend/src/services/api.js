import axios from 'axios'
import { postAccountSync } from './accountContracts'
import { isExactLoginPath, resolveLoginUrl } from '../utils/authRouting'

const API_BASE = import.meta.env.VITE_API_BASE || '/api'
const ACCESS_TOKEN_KEY = 'shizuha_access_token'
const REFRESH_TOKEN_KEY = 'shizuha_refresh_token'

// Track if we're currently refreshing to prevent multiple refresh calls
let isRefreshing = false
let refreshSubscribers = []

const api = axios.create({
  baseURL: API_BASE,
  headers: {
    'Content-Type': 'application/json',
  },
})

// Subscribe to token refresh
const subscribeTokenRefresh = (callback) => {
  refreshSubscribers.push(callback)
}

// Notify all subscribers that token has been refreshed
const onTokenRefreshed = (newToken) => {
  refreshSubscribers.forEach((callback) => callback(newToken))
  refreshSubscribers = []
}

// Refresh the access token
const refreshAccessToken = async () => {
  const refreshToken = localStorage.getItem(REFRESH_TOKEN_KEY)
  if (!refreshToken) {
    throw new Error('No refresh token')
  }

  const response = await fetch('/id/api/auth/refresh/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh: refreshToken }),
  })

  if (!response.ok) {
    throw new Error('Refresh failed')
  }

  const data = await response.json()
  localStorage.setItem(ACCESS_TOKEN_KEY, data.access)
  if (data.refresh) {
    localStorage.setItem(REFRESH_TOKEN_KEY, data.refresh)
  }

  return data.access
}

// Request interceptor - add auth token and debug logging
api.interceptors.request.use(
  (config) => {
    // Add Authorization header from localStorage
    const token = localStorage.getItem(ACCESS_TOKEN_KEY)
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    console.debug(`[API] ${config.method?.toUpperCase()} ${config.url}`)
    return config
  },
  (error) => Promise.reject(error)
)

// Response interceptor for error handling and auto-refresh
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config

    // If 401 and we haven't tried to refresh yet
    if (error.response?.status === 401 && !originalRequest._retry) {
      originalRequest._retry = true

      // If already refreshing, wait for it to complete
      if (isRefreshing) {
        return new Promise((resolve) => {
          subscribeTokenRefresh((newToken) => {
            originalRequest.headers.Authorization = `Bearer ${newToken}`
            resolve(api(originalRequest))
          })
        })
      }

      isRefreshing = true

      try {
        const newToken = await refreshAccessToken()
        isRefreshing = false
        onTokenRefreshed(newToken)
        originalRequest.headers.Authorization = `Bearer ${newToken}`
        return api(originalRequest)
      } catch (refreshError) {
        isRefreshing = false
        refreshSubscribers = []
        // Clear tokens and redirect to login
        localStorage.removeItem(ACCESS_TOKEN_KEY)
        localStorage.removeItem(REFRESH_TOKEN_KEY)
        localStorage.removeItem('shizuha_user')
        console.error('[API] Token refresh failed, redirecting to login')
        const loginUrl = resolveLoginUrl({
          basePath: import.meta.env.VITE_BASE_PATH,
          override: import.meta.env.VITE_LOGIN_URL,
        })
        // Avoid a reload loop when the auth check itself runs on the login page
        if (!isExactLoginPath(window.location.pathname, loginUrl, window.location.origin)) {
          window.location.href = loginUrl
        }
        return Promise.reject(refreshError)
      }
    }

    console.error('[API Error]', error.response?.data || error.message)
    return Promise.reject(error)
  }
)

// Auth API - uses accounts/me/ endpoint
export const authApi = {
  getUser: () => api.get('/accounts/me/'),
}

// Account API - note: 'accounts' plural to match DRF router
export const accountApi = {
  list: () => api.get('/accounts/'),
  get: (id) => api.get(`/accounts/${id}/`),
  create: (data) => api.post('/accounts/', data),
  update: (id, data) => api.patch(`/accounts/${id}/`, data),
  delete: (id) => api.delete(`/accounts/${id}/`),
  test: (id) => api.post(`/accounts/${id}/test/`),
  setDefault: (id) => api.post(`/accounts/${id}/set-default/`),
  sync: (id) => postAccountSync(api, id),
}

// Email provider presets API
export const providersApi = {
  list: () => api.get('/providers/'),
  detect: (email) => api.get('/providers/detect/', { params: { email } }),
}

// Folders API
export const foldersApi = {
  list: () => api.get('/folders/'),
  tree: () => api.get('/folders/'),  // Use list endpoint, tree structure built client-side
  sync: () => api.post('/folders/sync/'),
  get: (id) => api.get(`/folders/${id}/`),
  create: (data) => api.post('/folders/', data),
  update: (id, data) => api.patch(`/folders/${id}/`, data),
  delete: (id) => api.delete(`/folders/${id}/`),
}

// Messages API
export const messagesApi = {
  list: (params = {}) => api.get('/messages/', { params }),
  get: (id, params = {}) => api.get(`/messages/${id}/`, { params }),
  move: (data) => api.post('/messages/move/', data),
  flag: (data) => api.post('/messages/flag/', data),
  trash: (data) => api.post('/messages/trash/', data),
  delete: (data) => api.post('/messages/delete/', data),
  search: (data) => api.post('/messages/search/', data),
}

// Drafts API
export const draftsApi = {
  list: () => api.get('/drafts/'),
  get: (id) => api.get(`/drafts/${id}/`),
  create: (data) => api.post('/drafts/', data),
  update: (id, data) => api.patch(`/drafts/${id}/`, data),
  delete: (id) => api.delete(`/drafts/${id}/`),
  send: (id) => api.post(`/drafts/${id}/send/`),
}

// Send API
export const sendApi = {
  send: (data) => api.post('/send/', data),
}

// Contacts API
export const contactsApi = {
  list: (params = {}) => api.get('/contacts/', { params }),
  autocomplete: (q) => api.get('/contacts/autocomplete/', { params: { q } }),
  create: (data) => api.post('/contacts/', data),
  delete: (id) => api.delete(`/contacts/${id}/`),
}

// Filters API
export const filtersApi = {
  list: () => api.get('/filters/'),
  get: (id) => api.get(`/filters/${id}/`),
  create: (data) => api.post('/filters/', data),
  update: (id, data) => api.patch(`/filters/${id}/`, data),
  delete: (id) => api.delete(`/filters/${id}/`),
}

// Combined Mail API - for components that use mailApi
export const mailApi = {
  // Accounts (multi-account support)
  getAccounts: () => api.get('/accounts/'),
  getAccount: (id) => api.get(`/accounts/${id}/`),
  createAccount: (data) => api.post('/accounts/', data),
  updateAccount: (id, data) => api.patch(`/accounts/${id}/`, data),
  deleteAccount: (id) => api.delete(`/accounts/${id}/`),
  testAccount: (id) => api.post(`/accounts/${id}/test/`),
  testConnection: (data) => api.post('/accounts/test-connection/', data),
  setDefaultAccount: (id) => api.post(`/accounts/${id}/set-default/`),
  syncAccount: (id) => postAccountSync(api, id),

  // Providers
  getProviders: () => api.get('/providers/'),
  detectProvider: (email) => api.get('/providers/detect/', { params: { email } }),

  // Folders (optionally scoped to account)
  getFolders: (accountId) => api.get('/folders/', { params: accountId ? { account_id: accountId } : {} }),
  syncFolders: (accountId) => api.post('/folders/sync/', accountId ? { account_id: accountId } : {}),

  // Labels
  getLabels: () => api.get('/labels/'),

  // Messages (optionally scoped to account)
  getMessages: (params = {}) => api.get('/messages/', { params }),
  getMessage: (id, params = {}) => api.get(`/messages/${id}/`, { params }),
  getMessageBody: (id, params = {}) => api.get(`/messages/${id}/`, { params }),

  // Thread
  getThread: (threadId) => api.get(`/threads/${threadId}/`),

  // Send (with account_id for multi-account)
  sendEmail: (data) => api.post('/send/', data),

  // Search
  search: (params) => api.get('/messages/search/', { params }),
}

export default api
