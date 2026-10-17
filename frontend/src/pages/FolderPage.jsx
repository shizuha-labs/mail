import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { messagesApi, foldersApi } from '../services/api'
import { useMail } from '../contexts/MailContext'
import MessageList from '../components/MessageList'
import { Loader2 } from 'lucide-react'

const PAGE_SIZE = 50

const folderNames = {
  sent: 'Sent',
  drafts: 'Drafts',
  trash: 'Trash',
  junk: 'Spam',
  archive: 'Archive',
}

export default function FolderPage({ folderRole }) {
  const { folderId } = useParams()
  const queryClient = useQueryClient()
  const { folders, accounts, selectedAccount, isAllAccounts } = useMail()
  const [page, setPage] = useState(1)

  // Find current folder ID (from URL param or by role)
  const currentFolder = folderId
    ? folders?.find(f => f.id === folderId)
    : folders?.find(f => f.role === folderRole)
  const currentFolderId = currentFolder?.id

  const { data: folder } = useQuery({
    queryKey: ['folder', folderId],
    queryFn: () => foldersApi.get(folderId).then(r => r.data),
    enabled: !!folderId,
  })

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ['messages', folderRole || folderId, isAllAccounts ? 'all' : selectedAccount?.id, page],
    queryFn: async () => {
      if (isAllAccounts && folderRole && accounts?.length > 1) {
        // Fetch from all accounts in parallel
        const results = await Promise.allSettled(
          accounts.map(async (acc) => {
            const params = { folder_role: folderRole, account_id: acc.id, limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE }
            const response = await messagesApi.list(params)
            const messages = (response.data?.results || []).map(m => ({
              ...m,
              account_email: acc.email_address,
            }))
            return { ...response.data, results: messages }
          })
        )
        const allMessages = results
          .filter(r => r.status === 'fulfilled')
          .flatMap(r => r.value?.results || [])
          .sort((a, b) => new Date(b.date_received || b.date_sent || 0) - new Date(a.date_received || a.date_sent || 0))
        const totalCount = results
          .filter(r => r.status === 'fulfilled')
          .reduce((sum, r) => sum + (r.value?.count || r.value?.results?.length || 0), 0)
        return { results: allMessages, count: totalCount }
      }

      const params = { limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE }
      if (selectedAccount?.id) {
        params.account_id = selectedAccount.id
      }
      if (folderId) {
        params.folder = folderId
      } else if (folderRole) {
        params.folder_role = folderRole
      }
      const response = await messagesApi.list(params)
      return response.data
    },
    enabled: !!(folderId || folderRole) && (isAllAccounts ? !!accounts?.length : !!selectedAccount),
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

  const title = folderRole ? folderNames[folderRole] || folderRole : folder?.name || currentFolder?.name || 'Loading...'

  const totalCount = data?.count || 0
  const currentMessages = data?.results || []
  const totalPages = Math.ceil(totalCount / PAGE_SIZE)
  const showingFrom = currentMessages.length > 0 ? (page - 1) * PAGE_SIZE + 1 : 0
  const showingTo = showingFrom + currentMessages.length - 1

  return (
    <div className="h-full flex flex-col">
      <div className="p-4 border-b border-gray-200 dark:border-gray-800">
        <h1 className="text-xl font-semibold text-gray-900 dark:text-white">{title}</h1>
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
              folderId={currentFolderId}
              folderRole={folderRole}
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
