import assert from 'node:assert/strict'
import test from 'node:test'

const { postAccountSync } = await import('../../src/services/accountContracts.js')
const {
  getConnectionStatusPresentation,
} = await import('../../src/utils/connectionStatus.js')
const {
  isExactLoginPath,
  normalizeBasePath,
  resolveLoginUrl,
} = await import('../../src/utils/authRouting.js')

function captureNextRequest() {
  let captured
  const apiClient = {
    post: async (url, data) => {
      captured = { url, data }
      return { data: { status: 'sync_queued' } }
    },
  }
  return { apiClient, captured: () => captured }
}

test('account sync posts account_id to the collection action', async () => {
  const { apiClient, captured } = captureNextRequest()

  await postAccountSync(apiClient, 'account-123')

  assert.equal(captured().url, '/accounts/sync/')
  assert.deepEqual(captured().data, { account_id: 'account-123' })
})

test('action-required connection states have explicit labels', () => {
  assert.equal(
    getConnectionStatusPresentation('needs_reauth').label,
    'Re-authorization required',
  )
  assert.equal(
    getConnectionStatusPresentation('needs_attention').label,
    'Needs attention',
  )
  assert.equal(
    getConnectionStatusPresentation('future_status').label,
    'Not tested',
  )
})

test('standalone login target follows the normalized product base path', () => {
  assert.equal(normalizeBasePath('mail'), '/mail/')
  assert.equal(normalizeBasePath('/mail'), '/mail/')
  assert.equal(resolveLoginUrl({ basePath: '/mail/' }), '/mail/login')
  assert.equal(resolveLoginUrl({ basePath: '/' }), '/login')
})

test('explicit integrated login target overrides the standalone base path', () => {
  assert.equal(
    resolveLoginUrl({ basePath: '/mail/', override: '/id/login' }),
    '/id/login',
  )
})

test('login loop guard matches the exact route without prefix false positives', () => {
  assert.equal(isExactLoginPath('/mail/login', '/mail/login'), true)
  assert.equal(isExactLoginPath('/mail/login/', '/mail/login'), true)
  assert.equal(isExactLoginPath('/mail/login-help', '/mail/login'), false)
  assert.equal(isExactLoginPath('/mail/', '/mail/login'), false)
})
