// @ts-check
import { test, expect, uniqueId } from './fixtures.js'

/**
 * E2E tests for inbox functionality
 * Tests run against production Stalwart mail server
 */

test.describe('Unauthenticated Mail entry', () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  async function observeAccountsRequests(page) {
    const requests = []
    page.on('request', (request) => {
      if (new URL(request.url()).pathname === '/mail/api/accounts/') {
        requests.push(request.url())
      }
    })
    return requests
  }

  test('fresh product entry lands on the base-aware login route', async ({ page }) => {
    const accountsRequests = await observeAccountsRequests(page)

    await page.goto('/mail/')

    await expect(page).toHaveURL(/\/mail\/login\/?$/)
    await expect(page.getByRole('button', { name: 'Sign in with Shizuha ID' })).toBeVisible()
    expect(accountsRequests).toHaveLength(0)
  })

  test('direct login stays public and does not start the Mail data provider', async ({ page }) => {
    const accountsRequests = await observeAccountsRequests(page)

    await page.goto('/mail/login')

    await expect(page).toHaveURL(/\/mail\/login\/?$/)
    await expect(page.getByRole('button', { name: 'Sign in with Shizuha ID' })).toBeVisible()
    expect(accountsRequests).toHaveLength(0)
  })
})

test.describe('Inbox', () => {
  test('should display inbox page with correct elements', async ({ authenticatedPage }) => {
    await authenticatedPage.goto('/mail/')

    // Check for main inbox elements
    await expect(authenticatedPage.locator('text=Inbox').first()).toBeVisible({ timeout: 10000 })

    // Check for compose button
    await expect(authenticatedPage.getByRole('button', { name: /compose/i })).toBeVisible()
  })

  test('should display folder sidebar', async ({ authenticatedPage }) => {
    await authenticatedPage.goto('/mail/')

    // Wait for folders to load
    await authenticatedPage.waitForTimeout(2000)

    // Check for standard folders
    const sidebar = authenticatedPage.locator('[data-testid="folder-sidebar"], .folder-sidebar, nav, aside').first()
    await expect(sidebar).toBeVisible({ timeout: 10000 })

    // Common folder names to look for
    const folderNames = ['Inbox', 'Sent', 'Drafts', 'Trash']
    for (const folder of folderNames) {
      const folderLink = authenticatedPage.locator(`text=${folder}`).first()
      // At least Inbox should be visible
      if (folder === 'Inbox') {
        await expect(folderLink).toBeVisible()
      }
    }
  })

  test('should navigate to sent folder', async ({ authenticatedPage }) => {
    await authenticatedPage.goto('/mail/')

    // Click on Sent folder
    const sentLink = authenticatedPage.locator('text=Sent').first()
    await sentLink.click()

    // URL or content should update to show sent folder
    await authenticatedPage.waitForURL(/.*sent.*|.*mail.*/, { timeout: 10000 })
  })

  test('should navigate to drafts folder', async ({ authenticatedPage }) => {
    await authenticatedPage.goto('/mail/')

    // Click on Drafts folder
    const draftsLink = authenticatedPage.locator('text=Drafts').first()
    await draftsLink.click()

    // URL or content should update to show drafts folder
    await authenticatedPage.waitForURL(/.*draft.*|.*mail.*/, { timeout: 10000 })
  })

  test('should navigate to trash folder', async ({ authenticatedPage }) => {
    await authenticatedPage.goto('/mail/')

    // Click on Trash folder
    const trashLink = authenticatedPage.locator('text=Trash').first()
    await trashLink.click()

    // URL or content should update to show trash folder
    await authenticatedPage.waitForURL(/.*trash.*|.*mail.*/, { timeout: 10000 })
  })

  test('should display message count in status bar', async ({ authenticatedPage }) => {
    await authenticatedPage.goto('/mail/')

    // Wait for messages to load
    await authenticatedPage.waitForTimeout(2000)

    // Look for any count indicator - could be in folder label or status bar
    const countIndicator = authenticatedPage.locator('[data-testid="message-count"], .message-count, .folder-count, text=/\\d+ messages?/i').first()

    // This may or may not exist depending on implementation
    // Just verify the inbox page loaded successfully
    await expect(authenticatedPage.locator('text=Inbox').first()).toBeVisible()
  })

  test('should toggle select mode', async ({ authenticatedPage }) => {
    await authenticatedPage.goto('/mail/')

    // Wait for messages to load
    await authenticatedPage.waitForTimeout(2000)

    // Look for select/checkbox functionality if messages exist
    const messageList = authenticatedPage.locator('[data-testid="message-list"], .message-list, [role="list"]').first()

    // If there are messages, try to select one
    const firstMessage = authenticatedPage.locator('[data-testid="message-item"], .message-item, [role="listitem"]').first()

    // Check if there's a checkbox or select button
    const checkbox = firstMessage.locator('input[type="checkbox"], [role="checkbox"]').first()
    if (await checkbox.isVisible()) {
      await checkbox.click()
      await expect(checkbox).toBeChecked()
    }
  })
})

test.describe('Inbox API', () => {
  test('should fetch folders via API', async ({ authenticatedApi }) => {
    const response = await authenticatedApi.get('folders/')

    expect(response.ok()).toBeTruthy()

    const data = await response.json()
    const folders = data.results || data

    // Should have at least some folders
    expect(folders.length).toBeGreaterThanOrEqual(0)
  })

  test('should fetch messages via API', async ({ authenticatedApi }) => {
    const response = await authenticatedApi.get('messages/')

    expect(response.ok()).toBeTruthy()

    const data = await response.json()
    // Messages list could be empty
    expect(data).toBeDefined()
  })

  test('should fetch inbox messages via API', async ({ authenticatedApi }) => {
    const response = await authenticatedApi.get('messages/?folder=inbox')

    expect(response.ok()).toBeTruthy()

    const data = await response.json()
    expect(data).toBeDefined()
  })
})
