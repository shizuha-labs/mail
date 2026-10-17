export function postAccountSync(apiClient, accountId) {
  return apiClient.post('/accounts/sync/', { account_id: accountId })
}
