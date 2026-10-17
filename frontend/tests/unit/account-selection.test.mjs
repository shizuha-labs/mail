import assert from 'node:assert/strict'
import test from 'node:test'

const {
  initialAccountId,
  usableAccounts,
} = await import('../../src/utils/accountSelection.js')

test('starts on the canonical default instead of all accounts', () => {
  const accounts = [
    { id: 'legacy-provider', is_default: false },
    { id: 'managed-shizuha', is_default: true },
  ]

  assert.equal(initialAccountId(accounts), 'managed-shizuha')
})

test('falls back to the first account when no default is present', () => {
  assert.equal(initialAccountId([{ id: 'first' }, { id: 'second' }]), 'first')
  assert.equal(initialAccountId([]), null)
})

test('inactive audit rows never enter mailbox selection or all-accounts mode', () => {
  const accounts = [
    { id: 'retired', is_active: false, is_default: true },
    { id: 'managed', is_active: true, is_default: false },
  ]

  assert.deepEqual(usableAccounts(accounts).map((account) => account.id), ['managed'])
  assert.equal(initialAccountId(accounts), 'managed')
})
