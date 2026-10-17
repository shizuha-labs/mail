export function usableAccounts(accounts) {
  if (!Array.isArray(accounts)) return []
  return accounts.filter((account) => account.is_active !== false)
}

export function initialAccountId(accounts) {
  const usable = usableAccounts(accounts)
  if (usable.length === 0) return null
  return usable.find((account) => account.is_default)?.id || usable[0].id
}
