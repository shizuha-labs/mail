import { createContext, useContext, useState, useEffect, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { mailApi } from '../services/api'
import { initialAccountId, usableAccounts } from '../utils/accountSelection'

const MailContext = createContext(null)

export function MailProvider({ children }) {
  const queryClient = useQueryClient()
  const [selectedAccountId, setSelectedAccountId] = useState(null)
  const [selectedFolder, setSelectedFolder] = useState(null)
  const [selectedMessage, setSelectedMessage] = useState(null)
  const [composeOpen, setComposeOpen] = useState(false)
  const [composeData, setComposeData] = useState(null)

  // Fetch all accounts
  const {
    data: accounts,
    isLoading: accountsLoading,
    refetch: refetchAccounts,
  } = useQuery({
    queryKey: ['accounts'],
    queryFn: () => mailApi.getAccounts().then((r) => r.data),
    select: usableAccounts,
    retry: false,
  })

  // "All accounts" mode when selectedAccountId === 'all'
  const isAllAccounts = selectedAccountId === 'all'

  // Get selected account (or default) - null when "all accounts" mode
  const selectedAccount = isAllAccounts
    ? null
    : (accounts?.find((a) => a.id === selectedAccountId)
      || accounts?.find((a) => a.is_default)
      || accounts?.[0])

  // Set default selected account when accounts load
  useEffect(() => {
    if (accounts && accounts.length > 0 && !selectedAccountId) {
      // Start on the user's canonical default. "All accounts" remains an
      // explicit opt-in because one unavailable secondary provider must not
      // break or delay the managed Shizuha inbox.
      setSelectedAccountId(initialAccountId(accounts))
    }
  }, [accounts, selectedAccountId])

  // Fetch folders for selected account (use first account for folder structure in "all" mode)
  const folderAccountId = isAllAccounts ? accounts?.[0]?.id : selectedAccount?.id
  const {
    data: folders,
    isLoading: foldersLoading,
    refetch: refetchFolders,
  } = useQuery({
    queryKey: ['folders', folderAccountId],
    queryFn: () => mailApi.getFolders(folderAccountId).then((r) => r.data),
    enabled: !!folderAccountId,
  })

  // Set default folder to inbox when folders change
  useEffect(() => {
    if (folders && folders.length > 0) {
      const inbox = folders.find((f) => f.role === 'inbox')
      if (inbox && (!selectedFolder || selectedFolder.account_id !== selectedAccount?.id)) {
        setSelectedFolder(inbox)
      }
    }
  }, [folders, selectedAccount?.id])

  // Switch account
  const switchAccount = useCallback((accountId) => {
    setSelectedAccountId(accountId)
    setSelectedFolder(null)
    setSelectedMessage(null)
    // Invalidate queries to force refetch for new account
    queryClient.invalidateQueries({ queryKey: ['folders'] })
    queryClient.invalidateQueries({ queryKey: ['messages'] })
  }, [queryClient])

  // Set default account mutation
  const setDefaultAccountMutation = useMutation({
    mutationFn: (accountId) => mailApi.setDefaultAccount(accountId),
    onSuccess: () => {
      refetchAccounts()
    },
  })

  // Delete account mutation
  const deleteAccountMutation = useMutation({
    mutationFn: (accountId) => mailApi.deleteAccount(accountId),
    onSuccess: () => {
      refetchAccounts()
      // If we deleted the selected account, clear selection
      if (selectedAccountId === deleteAccountMutation.variables) {
        setSelectedAccountId(null)
        setSelectedFolder(null)
      }
    },
  })

  // Get folder by role
  const getFolderByRole = (role) => {
    if (!folders) return null
    return folders.find((f) => f.role === role)
  }

  // Calculate total unread across all accounts
  const totalUnread = accounts?.reduce((sum, account) => sum + (account.unread_count || 0), 0) || 0

  // Calculate unread for selected account
  const accountUnread = folders?.reduce((sum, f) => sum + (f.unread_messages || 0), 0) || 0

  // Update document title with unread count
  useEffect(() => {
    const unread = isAllAccounts ? totalUnread : accountUnread
    document.title = unread > 0 ? `(${unread}) Shizuha Mail` : 'Shizuha Mail'
  }, [totalUnread, accountUnread, isAllAccounts])

  // Open compose modal
  const openCompose = (data = null) => {
    setComposeData(data)
    setComposeOpen(true)
  }

  // Close compose modal
  const closeCompose = () => {
    setComposeOpen(false)
    setComposeData(null)
  }

  // Reply to message
  const replyTo = (message) => {
    openCompose({
      to: [message.from_address],
      subject: message.subject.startsWith('Re:') ? message.subject : `Re: ${message.subject}`,
      inReplyTo: message.message_id,
      accountId: message.account_id || selectedAccount?.id,
    })
  }

  // Forward message
  const forward = (message, body) => {
    openCompose({
      subject: message.subject.startsWith('Fwd:') ? message.subject : `Fwd: ${message.subject}`,
      body: `\n\n---------- Forwarded message ----------\nFrom: ${message.from_address?.email || message.from_address}\nDate: ${message.date_sent}\nSubject: ${message.subject}\n\n${body}`,
      accountId: message.account_id || selectedAccount?.id,
    })
  }

  // Check if user has any accounts configured
  const hasAccounts = accounts && accounts.length > 0

  const value = {
    // Multi-account support
    accounts,
    accountsLoading,
    refetchAccounts,
    selectedAccount,
    selectedAccountId,
    isAllAccounts,
    switchAccount,
    setDefaultAccount: setDefaultAccountMutation.mutate,
    deleteAccount: deleteAccountMutation.mutate,
    hasAccounts,

    // Folders (scoped to selected account)
    folders,
    foldersLoading,
    refetchFolders,
    selectedFolder,
    setSelectedFolder,
    getFolderByRole,

    // Messages
    selectedMessage,
    setSelectedMessage,

    // Unread counts
    totalUnread,
    accountUnread,

    // Compose
    composeOpen,
    composeData,
    openCompose,
    closeCompose,
    replyTo,
    forward,
  }

  return <MailContext.Provider value={value}>{children}</MailContext.Provider>
}

export function useMail() {
  const context = useContext(MailContext)
  if (!context) {
    throw new Error('useMail must be used within a MailProvider')
  }
  return context
}
