import { test as base } from '@playwright/test'

/**
 * Test fixtures for Shizuha Mail E2E tests
 * Uses production Stalwart server for actual email testing
 *
 * Authentication flow:
 * - Login happens at /id/login (Shizuha ID)
 * - After login, user is redirected to /mail/ (Shizuha Mail)
 * - Tokens are stored in localStorage with shared keys
 */

// Test user credentials (same as other Shizuha services)
export const TEST_USER = {
  username: 'testuser',
  password: 'TestPass123',
  email: 'testuser@example.com'
}

// Stalwart configuration (from environment or defaults)
export const STALWART_CONFIG = {
  jmapUrl: process.env.STALWART_JMAP_URL || 'https://34.0.13.211:8543',
  adminUrl: process.env.STALWART_ADMIN_URL || 'https://34.0.13.211:8543',
  adminUser: process.env.STALWART_ADMIN_USER || 'admin',
  adminPassword: process.env.STALWART_ADMIN_PASSWORD || 'Qwerty123!@#',
  verifySsl: process.env.STALWART_VERIFY_SSL !== 'false'
}

// Extend base test with authentication and mail fixtures
export const test = base.extend({
  // Authenticated page fixture - logs in via Shizuha ID
  authenticatedPage: async ({ page }, use) => {
    // Go to Shizuha ID login page with continue parameter
    await page.goto('/id/login?continue=/mail/')

    // Wait for form to be ready
    await page.waitForSelector('input[id="username"]', { timeout: 10000 })

    // Fill in credentials
    await page.fill('input[id="username"]', TEST_USER.username)
    await page.fill('input[id="password"]', TEST_USER.password)

    // Click submit
    await page.click('button[type="submit"]')

    // Wait for successful login API response
    await page.waitForResponse(
      response => response.url().includes('/auth/login/') && response.ok(),
      { timeout: 15000 }
    )

    // Wait for redirect to Mail
    await page.waitForURL(/.*mail.*/, { timeout: 30000 })

    // Wait for page to fully load
    await page.waitForLoadState('domcontentloaded', { timeout: 15000 })

    // Use the authenticated page
    await use(page)
  },

  // API context for Shizuha ID
  idApiContext: async ({ playwright }, use) => {
    const baseUrl = process.env.BASE_URL || 'http://shizuha-nginx'
    const apiContext = await playwright.request.newContext({
      baseURL: `${baseUrl}/id/api/`,
      extraHTTPHeaders: {
        'Content-Type': 'application/json',
      },
    })

    await use(apiContext)
    await apiContext.dispose()
  },

  // Authenticated API context - uses Shizuha ID for login
  authenticatedApi: async ({ playwright }, use) => {
    const baseUrl = process.env.BASE_URL || 'http://shizuha-nginx'
    const mailApiUrl = `${baseUrl}/mail/api/`
    const idApiUrl = `${baseUrl}/id/api/`

    const apiContext = await playwright.request.newContext({
      baseURL: idApiUrl,
      extraHTTPHeaders: {
        'Content-Type': 'application/json',
      },
    })

    // Login via Shizuha ID to get token
    const loginResponse = await apiContext.post('auth/login/', {
      data: {
        username: TEST_USER.username,
        password: TEST_USER.password
      }
    })

    // Check if login was successful
    if (!loginResponse.ok()) {
      const text = await loginResponse.text()
      throw new Error(`Login failed: ${loginResponse.status()} - ${text.substring(0, 200)}`)
    }

    const { tokens } = await loginResponse.json()

    // Create new context with auth header for Mail API
    const authContext = await playwright.request.newContext({
      baseURL: mailApiUrl,
      extraHTTPHeaders: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${tokens.access}`
      },
    })

    // Store the token for later use
    authContext.token = tokens.access

    await use(authContext)

    await apiContext.dispose()
    await authContext.dispose()
  },

  // Stalwart JMAP client context for direct mail operations
  stalwartApi: async ({ playwright, authenticatedApi }, use) => {
    // The authenticatedApi already has the JWT token
    // We can use this to authenticate with Stalwart via the backend
    // For direct Stalwart access, we'd need to get the user's mail token

    // Get mail accounts to verify connection
    const accountsResponse = await authenticatedApi.get('accounts/')
    let accounts = []
    if (accountsResponse.ok()) {
      const data = await accountsResponse.json()
      accounts = data.results || data || []
    }

    await use({
      accounts,
      api: authenticatedApi
    })
  },

  // Fixture to get or create test mailbox/folder
  testMailbox: async ({ authenticatedApi }, use) => {
    // Get existing folders
    const foldersResponse = await authenticatedApi.get('folders/')
    let folders = []

    if (foldersResponse.ok()) {
      const data = await foldersResponse.json()
      folders = data.results || data || []
    }

    // Find or use Inbox as default test folder
    const inbox = folders.find(f => f.role === 'inbox' || f.name.toLowerCase() === 'inbox')

    await use({
      folders,
      inbox,
    })
  },
})

export { expect } from '@playwright/test'

/**
 * Helper function to create unique test data
 */
export function uniqueId(prefix = 'test') {
  return `${prefix}_${Date.now()}_${Math.random().toString(36).substring(2, 7)}`
}

/**
 * Helper to wait for email to arrive (polls the API)
 */
export async function waitForEmail(api, subject, timeout = 30000) {
  const startTime = Date.now()

  while (Date.now() - startTime < timeout) {
    const response = await api.get('messages/?folder=inbox')
    if (response.ok()) {
      const data = await response.json()
      const messages = data.results || data || []
      const found = messages.find(m => m.subject === subject)
      if (found) return found
    }
    await new Promise(resolve => setTimeout(resolve, 1000))
  }

  throw new Error(`Email with subject "${subject}" not found within ${timeout}ms`)
}

/**
 * Helper to send a test email via API
 */
export async function sendTestEmail(api, { to, subject, body, cc, bcc }) {
  const response = await api.post('messages/send/', {
    data: {
      to: Array.isArray(to) ? to : [to],
      cc: cc ? (Array.isArray(cc) ? cc : [cc]) : undefined,
      bcc: bcc ? (Array.isArray(bcc) ? bcc : [bcc]) : undefined,
      subject,
      body,
    }
  })

  if (!response.ok()) {
    const text = await response.text()
    throw new Error(`Failed to send email: ${text}`)
  }

  return response.json()
}
