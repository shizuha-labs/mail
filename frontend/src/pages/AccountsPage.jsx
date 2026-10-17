import { useNavigate } from 'react-router-dom'
import { useMail } from '../contexts/MailContext'
import {
  Plus,
  Mail,
  Star,
  Trash2,
  Settings,
  CheckCircle,
  XCircle,
  AlertCircle,
  Loader2,
  RefreshCw,
} from 'lucide-react'
import { useMutation } from '@tanstack/react-query'
import { mailApi } from '../services/api'
import { getConnectionStatusPresentation } from '../utils/connectionStatus'
import { useState } from 'react'

const statusIcons = {
  success: CheckCircle,
  warning: AlertCircle,
  error: XCircle,
}

export default function AccountsPage() {
  const navigate = useNavigate()
  const { accounts, accountsLoading, refetchAccounts, setDefaultAccount, deleteAccount, switchAccount } = useMail()
  const [testingAccount, setTestingAccount] = useState(null)
  const [deleteConfirm, setDeleteConfirm] = useState(null)

  // Test connection mutation
  const testMutation = useMutation({
    mutationFn: (accountId) => {
      setTestingAccount(accountId)
      return mailApi.testAccount(accountId)
    },
    onSettled: () => {
      setTestingAccount(null)
      refetchAccounts()
    },
  })

  // Sync account mutation
  const syncMutation = useMutation({
    mutationFn: (accountId) => mailApi.syncAccount(accountId),
    onSuccess: () => {
      refetchAccounts()
    },
  })

  const handleDelete = (accountId) => {
    deleteAccount(accountId)
    setDeleteConfirm(null)
  }

  const handleSelectAccount = (account) => {
    switchAccount(account.id)
    navigate('/inbox')
  }

  if (accountsLoading) {
    return (
      <div className="h-full flex items-center justify-center">
        <Loader2 className="w-8 h-8 animate-spin text-brand-600" />
      </div>
    )
  }

  return (
    <div className="h-full overflow-y-auto bg-gray-50 dark:bg-gray-900">
      <div className="max-w-4xl mx-auto p-6">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Email Accounts</h1>
          <button
            onClick={() => navigate('/accounts/add')}
            className="flex items-center gap-2 px-4 py-2 bg-brand-600 text-white rounded-lg hover:bg-brand-700"
          >
            <Plus className="w-5 h-5" />
            Add Account
          </button>
        </div>

        {/* Empty state */}
        {(!accounts || accounts.length === 0) && (
          <div className="bg-white dark:bg-gray-800 rounded-lg p-12 text-center shadow-sm">
            <Mail className="w-16 h-16 mx-auto text-gray-400 mb-4" />
            <h2 className="text-xl font-semibold text-gray-900 dark:text-white mb-2">
              No email accounts configured
            </h2>
            <p className="text-gray-600 dark:text-gray-400 mb-6">
              Add your first email account to start sending and receiving emails.
            </p>
            <button
              onClick={() => navigate('/accounts/add')}
              className="inline-flex items-center gap-2 px-6 py-3 bg-brand-600 text-white rounded-lg hover:bg-brand-700"
            >
              <Plus className="w-5 h-5" />
              Add Email Account
            </button>
          </div>
        )}

        {/* Account list */}
        {accounts && accounts.length > 0 && (
          <div className="space-y-4">
            {accounts.map((account) => {
              const status = getConnectionStatusPresentation(account.connection_status)
              const StatusIcon = statusIcons[status.icon]

              return (
                <div
                  key={account.id}
                  className="bg-white dark:bg-gray-800 rounded-lg p-6 shadow-sm"
                >
                  <div className="flex items-start justify-between">
                    <div
                      className="flex items-start gap-4 flex-1 cursor-pointer"
                      onClick={() => handleSelectAccount(account)}
                    >
                      {/* Avatar */}
                      <div className="w-12 h-12 rounded-full bg-brand-100 dark:bg-brand-900/30 flex items-center justify-center">
                        <Mail className="w-6 h-6 text-brand-600" />
                      </div>

                      {/* Info */}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <h3 className="font-semibold text-gray-900 dark:text-white truncate">
                            {account.display_name || account.email_address.split('@')[0]}
                          </h3>
                          {account.is_default && (
                            <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400 text-xs rounded-full">
                              <Star className="w-3 h-3" />
                              Default
                            </span>
                          )}
                        </div>
                        <p className="text-gray-600 dark:text-gray-400 truncate">
                          {account.email_address}
                        </p>
                        <div className="flex items-center gap-4 mt-2 text-sm">
                          <span className={`flex items-center gap-1 ${status.color}`}>
                            <StatusIcon className="w-4 h-4" />
                            {status.label}
                          </span>
                          {account.unread_count > 0 && (
                            <span className="text-gray-600 dark:text-gray-400">
                              {account.unread_count} unread
                            </span>
                          )}
                          <span className="text-gray-500 dark:text-gray-500">
                            {account.incoming_server_type.toUpperCase()} / {account.incoming_host}
                          </span>
                        </div>
                        {account.connection_error && (
                          <p className="mt-2 text-sm text-red-600 dark:text-red-400">
                            {account.connection_error}
                          </p>
                        )}
                      </div>
                    </div>

                    {/* Actions */}
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => testMutation.mutate(account.id)}
                        disabled={testingAccount === account.id}
                        className="p-2 text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg"
                        title="Test connection"
                      >
                        {testingAccount === account.id ? (
                          <Loader2 className="w-5 h-5 animate-spin" />
                        ) : (
                          <RefreshCw className="w-5 h-5" />
                        )}
                      </button>
                      {!account.is_default && (
                        <button
                          onClick={() => setDefaultAccount(account.id)}
                          className="p-2 text-gray-500 hover:text-amber-500 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg"
                          title="Set as default"
                        >
                          <Star className="w-5 h-5" />
                        </button>
                      )}
                      <button
                        onClick={() => navigate('/settings')}
                        className="p-2 text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg"
                        title="Settings"
                      >
                        <Settings className="w-5 h-5" />
                      </button>
                      <button
                        onClick={() => setDeleteConfirm(account.id)}
                        className="p-2 text-gray-500 hover:text-red-500 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg"
                        title="Remove account"
                      >
                        <Trash2 className="w-5 h-5" />
                      </button>
                    </div>
                  </div>

                  {/* Delete confirmation */}
                  {deleteConfirm === account.id && (
                    <div className="mt-4 p-4 bg-red-50 dark:bg-red-900/20 rounded-lg border border-red-200 dark:border-red-800">
                      <p className="text-red-700 dark:text-red-400 mb-3">
                        Are you sure you want to remove this account? This will delete all cached messages.
                      </p>
                      <div className="flex gap-2">
                        <button
                          onClick={() => handleDelete(account.id)}
                          className="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700"
                        >
                          Remove Account
                        </button>
                        <button
                          onClick={() => setDeleteConfirm(null)}
                          className="px-4 py-2 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg"
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
