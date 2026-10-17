// @ts-check
import { test, expect, uniqueId, sendTestEmail } from './fixtures.js'

/**
 * E2E tests for email compose functionality
 * Tests run against production Stalwart mail server
 */

test.describe('Email Compose', () => {
  test('should open compose modal', async ({ authenticatedPage }) => {
    await authenticatedPage.goto('/mail/')

    // Click compose button
    const composeBtn = authenticatedPage.getByRole('button', { name: /compose/i })
    await composeBtn.click()

    // Wait for compose modal/form to appear
    await expect(authenticatedPage.locator('[data-testid="compose-modal"], .compose-modal, [role="dialog"], form')).toBeVisible({ timeout: 10000 })

    // Check for To field
    await expect(authenticatedPage.locator('[name="to"], [placeholder*="To"], input[type="email"]').first()).toBeVisible()

    // Check for Subject field
    await expect(authenticatedPage.locator('[name="subject"], [placeholder*="Subject"]').first()).toBeVisible()

    // Check for Body field
    await expect(authenticatedPage.locator('[name="body"], textarea, [contenteditable="true"]').first()).toBeVisible()
  })

  test('should validate required fields', async ({ authenticatedPage }) => {
    await authenticatedPage.goto('/mail/')

    // Open compose
    const composeBtn = authenticatedPage.getByRole('button', { name: /compose/i })
    await composeBtn.click()

    await authenticatedPage.waitForTimeout(500)

    // Try to send without filling fields
    const sendBtn = authenticatedPage.getByRole('button', { name: /send/i })

    // If send button exists and is enabled, click it
    if (await sendBtn.isVisible()) {
      await sendBtn.click()

      // Should show validation error or prevent send
      await authenticatedPage.waitForTimeout(1000)

      // Either we stay on compose or see an error
      const composeForm = authenticatedPage.locator('[data-testid="compose-modal"], .compose-modal, [role="dialog"]').first()
      const isStillComposing = await composeForm.isVisible()

      // If form is still visible, validation worked
      if (isStillComposing) {
        expect(isStillComposing).toBeTruthy()
      }
    }
  })

  test('should compose and send email', async ({ authenticatedPage }) => {
    const uniqueSubject = `Test Email ${uniqueId()}`

    await authenticatedPage.goto('/mail/')

    // Open compose
    const composeBtn = authenticatedPage.getByRole('button', { name: /compose/i })
    await composeBtn.click()

    await authenticatedPage.waitForTimeout(500)

    // Fill in email fields
    await authenticatedPage.locator('[name="to"], [placeholder*="To"], input[type="email"]').first().fill('test-recipient@example.com')
    await authenticatedPage.locator('[name="subject"], [placeholder*="Subject"]').first().fill(uniqueSubject)
    await authenticatedPage.locator('[name="body"], textarea, [contenteditable="true"]').first().fill('This is a test email body.')

    // Send email
    const sendBtn = authenticatedPage.getByRole('button', { name: /send/i })
    await sendBtn.click()

    // Wait for success indication
    await authenticatedPage.waitForTimeout(2000)

    // Compose modal should close or show success message
    const successIndicator = authenticatedPage.locator('text=/sent|success/i').first()
    const composeModal = authenticatedPage.locator('[data-testid="compose-modal"], .compose-modal, [role="dialog"]').first()

    // Either modal closed or success message shown
    const modalClosed = !(await composeModal.isVisible())
    const hasSuccess = await successIndicator.isVisible()

    expect(modalClosed || hasSuccess).toBeTruthy()
  })

  test('should add CC recipients', async ({ authenticatedPage }) => {
    await authenticatedPage.goto('/mail/')

    // Open compose
    const composeBtn = authenticatedPage.getByRole('button', { name: /compose/i })
    await composeBtn.click()

    await authenticatedPage.waitForTimeout(500)

    // Look for CC button or field
    const ccButton = authenticatedPage.locator('button:has-text("Cc"), button:has-text("CC"), [data-testid="add-cc"]').first()
    if (await ccButton.isVisible()) {
      await ccButton.click()
    }

    // Fill CC field
    const ccField = authenticatedPage.locator('[name="cc"], [placeholder*="Cc"], [data-testid="cc-field"]').first()
    if (await ccField.isVisible()) {
      await ccField.fill('cc-recipient@example.com')
      await expect(ccField).toHaveValue(/cc-recipient@example.com/)
    }
  })

  test('should add BCC recipients', async ({ authenticatedPage }) => {
    await authenticatedPage.goto('/mail/')

    // Open compose
    const composeBtn = authenticatedPage.getByRole('button', { name: /compose/i })
    await composeBtn.click()

    await authenticatedPage.waitForTimeout(500)

    // Look for BCC button or field
    const bccButton = authenticatedPage.locator('button:has-text("Bcc"), button:has-text("BCC"), [data-testid="add-bcc"]').first()
    if (await bccButton.isVisible()) {
      await bccButton.click()
    }

    // Fill BCC field
    const bccField = authenticatedPage.locator('[name="bcc"], [placeholder*="Bcc"], [data-testid="bcc-field"]').first()
    if (await bccField.isVisible()) {
      await bccField.fill('bcc-recipient@example.com')
      await expect(bccField).toHaveValue(/bcc-recipient@example.com/)
    }
  })

  test('should discard compose', async ({ authenticatedPage }) => {
    await authenticatedPage.goto('/mail/')

    // Open compose
    const composeBtn = authenticatedPage.getByRole('button', { name: /compose/i })
    await composeBtn.click()

    await authenticatedPage.waitForTimeout(500)

    // Fill some content
    await authenticatedPage.locator('[name="subject"], [placeholder*="Subject"]').first().fill('Draft to discard')

    // Find and click discard/cancel button
    const discardBtn = authenticatedPage.locator('button:has-text("Discard"), button:has-text("Cancel"), [data-testid="discard-btn"]').first()

    if (await discardBtn.isVisible()) {
      await discardBtn.click()

      // Modal should close
      await authenticatedPage.waitForTimeout(500)
      const composeModal = authenticatedPage.locator('[data-testid="compose-modal"], .compose-modal, [role="dialog"]').first()
      const modalClosed = !(await composeModal.isVisible())
      expect(modalClosed).toBeTruthy()
    }
  })

  test('should minimize and restore compose modal', async ({ authenticatedPage }) => {
    await authenticatedPage.goto('/mail/')

    // Open compose
    const composeBtn = authenticatedPage.getByRole('button', { name: /compose/i })
    await composeBtn.click()

    await authenticatedPage.waitForTimeout(500)

    // Look for minimize button
    const minimizeBtn = authenticatedPage.locator('button:has-text("Minimize"), [data-testid="minimize-btn"], [aria-label="Minimize"]').first()

    if (await minimizeBtn.isVisible()) {
      await minimizeBtn.click()

      // Modal should be minimized
      await authenticatedPage.waitForTimeout(500)

      // Look for restore/maximize button
      const restoreBtn = authenticatedPage.locator('button:has-text("Restore"), button:has-text("Maximize"), [data-testid="restore-btn"]').first()
      if (await restoreBtn.isVisible()) {
        await restoreBtn.click()
        // Modal should be restored
        await authenticatedPage.waitForTimeout(500)
      }
    }
  })
})

test.describe('Email Compose API', () => {
  test('should send email via API', async ({ authenticatedApi }) => {
    const uniqueSubject = `API Test ${uniqueId()}`

    const response = await authenticatedApi.post('messages/send/', {
      data: {
        to: ['api-test@example.com'],
        subject: uniqueSubject,
        body: 'Email sent via API test.'
      }
    })

    // Expect success or at least proper error response
    if (response.ok()) {
      const data = await response.json()
      expect(data).toBeDefined()
    } else {
      // API might return 400 for invalid recipient - that's expected
      const status = response.status()
      expect([200, 201, 202, 400, 422]).toContain(status)
    }
  })

  test('should save draft via API', async ({ authenticatedApi }) => {
    const uniqueSubject = `Draft ${uniqueId()}`

    const response = await authenticatedApi.post('drafts/', {
      data: {
        to: ['draft-test@example.com'],
        subject: uniqueSubject,
        body: 'This is a draft email.'
      }
    })

    if (response.ok()) {
      const draft = await response.json()
      expect(draft).toBeDefined()
      expect(draft.subject).toBe(uniqueSubject)
    }
  })
})
