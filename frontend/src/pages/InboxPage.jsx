import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { messagesApi } from '../services/api'
import { useMail } from '../contexts/MailContext'
import MessageList from '../components/MessageList'
import { Loader2, Mail, Plus, X } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { parseSearchQuery, getSearchChips } from '../utils/searchParser'

const PAGE_SIZE = 50

export default function InboxPage() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const search = searchParams.get('search')
  const { folders, accounts, selectedAccount, isAllAccounts, hasAccounts } = useMail()
  const [page, setPage] = useState(1)

  const inboxFolder = folders?.find(f => f.role === 'inbox')

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ['messages', 'inbox', isAllAccounts ? 'all' : selectedAccount?.id, search, page],
    queryFn: async () => {
      if (isAllAccounts && accounts?.length > 1) {
        // Fetch inbox from all accounts in parallel and merge
        const results = await Promise.allSettled(
          accounts.map(async (acc) => {
            const params = { folder_role: 'inbox', account_id: acc.id, limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE }
            const response = await messagesApi.list(params)
            // Tag each message with its account email for display
            const messages = (response.data?.results || []).map(m => ({
              ...m,
              account_email: acc.email_address,
            }))
            return { ...response.data, results: messages }
          })
        )
        // Merge results, sorted by date descending
        const allMessages = results
          .filter(r => r.status === 'fulfilled')
          .flatMap(r => r.value?.results || [])
          .sort((a, b) => new Date(b.date_received || b.date_sent || 0) - new Date(a.date_received || a.date_sent || 0))
        const totalCount = results
          .filter(r => r.status === 'fulfilled')
          .reduce((sum, r) => sum + (r.value?.count || r.value?.results?.length || 0), 0)
        return { results: allMessages, count: totalCount }
      }

      const params = { folder_role: 'inbox', limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE }
      if (selectedAccount?.id) {
        params.account_id = selectedAccount.id
      }
      if (search) {
        const searchParams = parseSearchQuery(search)
        Object.assign(params, searchParams)
        const response = await messagesApi.search(params)
        return response.data
      }
      const response = await messagesApi.list(params)
      return response.data
    },
    enabled: isAllAccounts ? !!accounts?.length : !!selectedAccount,
    keepPreviousData: true,
  })

  const flagMutation = useMutation({
    mutationFn: ({ messageIds, isFlagged, accountId }) =>
      messagesApi.flag({
        message_ids: messageIds,
        is_flagged: isFlagged,
        account_id: accountId || selectedAccount?.id,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['messages'] })
    },
  })

  const handleToggleFlag = (messageId, isFlagged, accountId) => {
    flagMutation.mutate({ messageIds: [messageId], isFlagged, accountId })
  }

  // No accounts configured - show setup prompt
  if (!hasAccounts) {
    return (
      <div className="h-full flex items-center justify-center">
        <div className="text-center p-8">
          <Mail className="w-16 h-16 mx-auto text-gray-400 mb-4" />
          <h2 className="text-xl font-semibold text-gray-900 dark:text-white mb-2">
            Welcome to Shizuha Mail
          </h2>
          <p className="text-gray-600 dark:text-gray-400 mb-6 max-w-md">
            Connect your email accounts to start sending and receiving emails.
            We support Gmail, Outlook, Yahoo, and any IMAP/SMTP provider.
          </p>
          <button
            onClick={() => navigate('/accounts/add')}
            className="inline-flex items-center gap-2 px-6 py-3 bg-brand-600 text-white rounded-lg hover:bg-brand-700"
          >
            <Plus className="w-5 h-5" />
            Add Email Account
          </button>
        </div>
      </div>
    )
  }

  const totalCount = data?.count || 0
  const currentMessages = data?.results || []
  const totalPages = Math.ceil(totalCount / PAGE_SIZE)
  const showingFrom = currentMessages.length > 0 ? (page - 1) * PAGE_SIZE + 1 : 0
  const showingTo = showingFrom + currentMessages.length - 1

  return (
    <div className="h-full flex flex-col">
      <div className="p-4 border-b border-gray-200 dark:border-gray-800">
        <h1 className="text-xl font-semibold text-gray-900 dark:text-white">
          {search ? 'Search Results' : 'Inbox'}
        </h1>
        {search && (
          <div className="flex flex-wrap items-center gap-2 mt-2">
            {getSearchChips(search).map((chip, i) => (
              <span key={i} className="inline-flex items-center gap-1 px-2 py-0.5 bg-brand-100 dark:bg-brand-900/30 text-brand-800 dark:text-brand-300 rounded-full text-xs font-medium">
                {chip.operator}:{chip.value}
              </span>
            ))}
            {parseSearchQuery(search).query && (
              <span className="text-sm text-gray-600 dark:text-gray-400">
                "{parseSearchQuery(search).query}"
              </span>
            )}
            <button
              onClick={() => navigate('/inbox')}
              className="inline-flex items-center gap-1 px-2 py-0.5 text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
            >
              <X className="w-3 h-3" />
              Clear
            </button>
          </div>
        )}
        {totalCount > 0 && (
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
            {showingFrom}-{showingTo} of {totalCount} messages
          </p>
        )}
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center h-64">
          <Loader2 className="w-8 h-8 animate-spin text-brand-600" />
        </div>
      ) : (
        <>
          <div className="flex-1 overflow-y-auto">
            <MessageList
              messages={currentMessages}
              onToggleFlag={handleToggleFlag}
              folderId={inboxFolder?.id}
              showAccount={isAllAccounts}
            />
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between px-4 py-3 border-t border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900">
              <button
                onClick={() => setPage(p => Math.max(1, p - 1))}
                disabled={page <= 1 || isFetching}
                className="px-3 py-1.5 text-sm font-medium text-gray-700 dark:text-gray-300 bg-gray-100 dark:bg-gray-800 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-700 disabled:opacity-50"
              >
                Previous
              </button>
              <span className="text-sm text-gray-500 dark:text-gray-400">
                Page {page} of {totalPages}
              </span>
              <button
                onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                disabled={page >= totalPages || isFetching}
                className="px-3 py-1.5 text-sm font-medium text-gray-700 dark:text-gray-300 bg-gray-100 dark:bg-gray-800 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-700 disabled:opacity-50"
              >
                Next
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
