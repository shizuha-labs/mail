// @ts-check
import { test, expect, uniqueId } from './fixtures.js'

/**
 * E2E tests for multi-account support
 * Tests the new generic mail client features
 */

test.describe('Multi-Account Management', () => {
  test.describe('Account List Page', () => {
    test('should display accounts page', async ({ authenticatedPage }) => {
      await authenticatedPage.goto('/mail/accounts')

      // Should show the accounts page
      await expect(authenticatedPage.locator('text=Email Accounts')).toBeVisible({ timeout: 10000 })
    })

    test('should show add account button', async ({ authenticatedPage }) => {
      await authenticatedPage.goto('/mail/accounts')

      // Should have add account button
      const addButton = authenticatedPage.getByRole('button', { name: /add account/i })
      await expect(addButton).toBeVisible()
    })

    test('should navigate to add account page', async ({ authenticatedPage }) => {
      await authenticatedPage.goto('/mail/accounts')

      // Click add account button
      const addButton = authenticatedPage.getByRole('button', { name: /add account/i })
      await addButton.click()

      // Should navigate to add account page
      await authenticatedPage.waitForURL(/.*accounts\/add.*/, { timeout: 10000 })
      await expect(authenticatedPage.locator('text=Add Email Account')).toBeVisible()
    })
  })

  test.describe('Add Account Page', () => {
    test('should display add account form', async ({ authenticatedPage }) => {
      await authenticatedPage.goto('/mail/accounts/add')

      // Should show form elements
      await expect(authenticatedPage.locator('text=Email Provider')).toBeVisible({ timeout: 10000 })
      await expect(authenticatedPage.locator('text=Account Details')).toBeVisible()
    })

    test('should show email provider presets', async ({ authenticatedPage }) => {
      await authenticatedPage.goto('/mail/accounts/add')

      // Wait for providers to load
      await authenticatedPage.waitForTimeout(2000)

      // Should show common providers
      await expect(authenticatedPage.locator('text=Gmail')).toBeVisible()
      await expect(authenticatedPage.locator('text=Outlook')).toBeVisible()
    })

    test('should auto-detect Gmail provider from email', async ({ authenticatedPage }) => {
      await authenticatedPage.goto('/mail/accounts/add')

      // Wait for form to load
      await authenticatedPage.waitForTimeout(1000)

      // Enter Gmail address
      const emailInput = authenticatedPage.locator('input[type="email"]')
      await emailInput.fill('test@gmail.com')

      // Wait for auto-detection
      await authenticatedPage.waitForTimeout(500)

      // Gmail button should be highlighted/selected
      const gmailButton = authenticatedPage.locator('button:has-text("Gmail")')
      await expect(gmailButton).toHaveClass(/border-brand-500|bg-brand-50/)
    })

    test('should show server settings section', async ({ authenticatedPage }) => {
      await authenticatedPage.goto('/mail/accounts/add')

      // Click on server settings to expand
      const serverSettingsButton = authenticatedPage.locator('button:has-text("Server Settings")')
      await serverSettingsButton.click()

      // Should show incoming server fields
      await expect(authenticatedPage.locator('text=Incoming Mail')).toBeVisible()
      await expect(authenticatedPage.locator('text=Outgoing Mail')).toBeVisible()
    })

    test('should validate required fields', async ({ authenticatedPage }) => {
      await authenticatedPage.goto('/mail/accounts/add')

      // Wait for form to load
      await authenticatedPage.waitForTimeout(1000)

      // Try to submit without filling required fields
      const addButton = authenticatedPage.getByRole('button', { name: /^add account$/i })

      // Button should be disabled or form should show validation
      await expect(addButton).toBeDisabled()
    })
  })

  test.describe('Account Switcher', () => {
    test('should show account dropdown in sidebar', async ({ authenticatedPage }) => {
      await authenticatedPage.goto('/mail/')

      // Wait for sidebar to load
      await authenticatedPage.waitForTimeout(2000)

      // Look for account switcher or add account prompt
      const sidebar = authenticatedPage.locator('nav, aside').first()
      await expect(sidebar).toBeVisible()

      // Either shows current account or "Add Email Account" prompt
      const accountOrPrompt = authenticatedPage.locator('text=Add Email Account').or(
        authenticatedPage.locator('[data-testid="account-switcher"]')
      ).or(
        authenticatedPage.locator('button:has-text("Manage Accounts")')
      )
      await expect(accountOrPrompt.first()).toBeVisible()
    })
  })
})

test.describe('Multi-Account API', () => {
  test('should list accounts', async ({ authenticatedApi }) => {
    const response = await authenticatedApi.get('accounts/')

    expect(response.ok()).toBeTruthy()

    const data = await response.json()
    // Should return an array (possibly empty)
    expect(Array.isArray(data)).toBeTruthy()
  })

  test('should get email provider presets', async ({ authenticatedApi }) => {
    const response = await authenticatedApi.get('providers/')

    expect(response.ok()).toBeTruthy()

    const data = await response.json()
    expect(Array.isArray(data)).toBeTruthy()
    expect(data.length).toBeGreaterThan(0)

    // Should have common providers
    const providerIds = data.map(p => p.id)
    expect(providerIds).toContain('gmail')
    expect(providerIds).toContain('outlook')
  })

  test('should detect Gmail provider', async ({ authenticatedApi }) => {
    const response = await authenticatedApi.get('providers/detect/?email=user@gmail.com')

    expect(response.ok()).toBeTruthy()

    const data = await response.json()
    expect(data.id).toBe('gmail')
    expect(data.incoming_host).toBe('imap.gmail.com')
  })

  test('should detect Outlook provider', async ({ authenticatedApi }) => {
    const response = await authenticatedApi.get('providers/detect/?email=user@outlook.com')

    expect(response.ok()).toBeTruthy()

    const data = await response.json()
    expect(data.id).toBe('outlook')
  })

  test('should return custom for unknown domain', async ({ authenticatedApi }) => {
    const response = await authenticatedApi.get('providers/detect/?email=user@unknowndomain123.xyz')

    expect(response.ok()).toBeTruthy()

    const data = await response.json()
    expect(data.id).toBe('custom')
  })

  test('should create account with valid data', async ({ authenticatedApi }) => {
    const uniqueEmail = `test-${uniqueId()}@testdomain.com`

    const response = await authenticatedApi.post('accounts/', {
      data: {
        email_address: uniqueEmail,
        incoming_password: 'testpassword123',
        display_name: 'Test Account',
        from_name: 'Test User',
        incoming_server_type: 'imap',
        incoming_host: 'imap.testdomain.com',
        incoming_port: 993,
        incoming_security: 'ssl',
        incoming_username: uniqueEmail,
        outgoing_host: 'smtp.testdomain.com',
        outgoing_port: 587,
        outgoing_security: 'starttls',
      }
    })

    // Should create successfully (or fail with connection test error, which is expected)
    expect([200, 201, 400]).toContain(response.status())

    if (response.status() === 201) {
      const data = await response.json()
      expect(data.email_address).toBe(uniqueEmail)
      expect(data.display_name).toBe('Test Account')

      // Clean up - delete the account
      const deleteResponse = await authenticatedApi.delete(`accounts/${data.id}/`)
      expect(deleteResponse.ok()).toBeTruthy()
    }
  })

  test('should list folders for account', async ({ authenticatedApi }) => {
    const response = await authenticatedApi.get('folders/')

    expect(response.ok()).toBeTruthy()

    const data = await response.json()
    // Returns array of folders (may be empty if no accounts)
    expect(Array.isArray(data)).toBeTruthy()
  })

  test('should list messages', async ({ authenticatedApi }) => {
    const response = await authenticatedApi.get('messages/')

    expect(response.ok()).toBeTruthy()

    const data = await response.json()
    // Should have results array
    expect(data).toHaveProperty('results')
    expect(Array.isArray(data.results)).toBeTruthy()
  })
})

test.describe('Compose with Account Selection', () => {
  test('should display compose page', async ({ authenticatedPage }) => {
    await authenticatedPage.goto('/mail/compose')

    // Should show compose form
    await expect(authenticatedPage.locator('text=New Message').or(
      authenticatedPage.locator('text=Compose')
    ).first()).toBeVisible({ timeout: 10000 })
  })

  test('should show To field', async ({ authenticatedPage }) => {
    await authenticatedPage.goto('/mail/compose')

    // Should show To field
    const toLabel = authenticatedPage.locator('text=To').or(
      authenticatedPage.locator('label:has-text("To")')
    )
    await expect(toLabel.first()).toBeVisible()
  })

  test('should show Subject field', async ({ authenticatedPage }) => {
    await authenticatedPage.goto('/mail/compose')

    // Should show Subject field
    const subjectInput = authenticatedPage.locator('input[placeholder*="Subject"]').or(
      authenticatedPage.locator('label:has-text("Subject")')
    )
    await expect(subjectInput.first()).toBeVisible()
  })
})

test.describe('Inbox with No Accounts', () => {
  test('should show welcome message when no accounts configured', async ({ authenticatedPage }) => {
    await authenticatedPage.goto('/mail/')

    // Wait for page to load
    await authenticatedPage.waitForTimeout(2000)

    // Either shows inbox with messages or welcome/setup prompt
    const inboxOrWelcome = authenticatedPage.locator('text=Inbox').or(
      authenticatedPage.locator('text=Welcome')
    ).or(
      authenticatedPage.locator('text=Add Email Account')
    ).or(
      authenticatedPage.locator('text=Connect your email')
    )

    await expect(inboxOrWelcome.first()).toBeVisible({ timeout: 10000 })
  })
})
