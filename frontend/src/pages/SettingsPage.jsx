import { useState, useEffect } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useMail } from '../contexts/MailContext'
import { mailApi } from '../services/api'
import { Save, Loader2, RefreshCw, Sun, Moon, Monitor } from 'lucide-react'
import clsx from 'clsx'
import useTheme from '../hooks/useTheme'

export default function SettingsPage() {
  const queryClient = useQueryClient()
  const { selectedAccount, accountsLoading, refetchFolders } = useMail()
  const { theme, setTheme, isSystem } = useTheme()
  const [signature, setSignature] = useState('')

  useEffect(() => {
    if (selectedAccount) {
      setSignature(selectedAccount.signature || '')
    }
  }, [selectedAccount])

  const updateMutation = useMutation({
    mutationFn: (data) => mailApi.updateAccount(selectedAccount?.id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['accounts'] })
    },
  })

  const handleSave = () => {
    updateMutation.mutate({ signature })
  }

  const handleSyncFolders = async () => {
    try {
      await mailApi.syncFolders(selectedAccount?.id)
      refetchFolders()
    } catch (error) {
      console.error('Failed to sync folders:', error)
    }
  }

  if (accountsLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="w-8 h-8 animate-spin text-brand-600" />
      </div>
    )
  }

  return (
    <div className="max-w-2xl mx-auto p-6">
      <h1 className="text-2xl font-semibold text-gray-900 dark:text-white mb-6">Settings</h1>

      {/* Appearance */}
      <section className="mb-8">
        <h2 className="text-lg font-medium text-gray-900 dark:text-white mb-4">Appearance</h2>
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
          <label className="text-sm text-gray-500 dark:text-gray-400 mb-3 block">Theme</label>
          <div className="flex gap-3">
            {[
              { value: 'light', label: 'Light', icon: Sun },
              { value: 'dark', label: 'Dark', icon: Moon },
              { value: 'system', label: 'System', icon: Monitor },
            ].map(({ value, label, icon: Icon }) => {
              const isActive = value === 'system' ? isSystem : (!isSystem && theme === value)
              return (
                <button
                  key={value}
                  onClick={() => setTheme(value)}
                  className={clsx(
                    'flex items-center gap-2 px-4 py-2 rounded-lg border text-sm font-medium transition-colors',
                    isActive
                      ? 'border-brand-600 bg-brand-50 text-brand-700 dark:bg-brand-900/20 dark:text-brand-300 dark:border-brand-500'
                      : 'border-gray-200 dark:border-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700'
                  )}
                >
                  <Icon className="w-4 h-4" />
                  {label}
                </button>
              )
            })}
          </div>
        </div>
      </section>

      {!selectedAccount ? (
        <p className="text-gray-500 dark:text-gray-400">No account selected. Add an account to configure account settings.</p>
      ) : (
        <>
          {/* Account Info */}
          <section className="mb-8">
            <h2 className="text-lg font-medium text-gray-900 dark:text-white mb-4">Account</h2>
            <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
              <div className="space-y-3">
                <div>
                  <label className="text-sm text-gray-500 dark:text-gray-400">Email Address</label>
                  <p className="text-gray-900 dark:text-white">{selectedAccount.email_address}</p>
                </div>
                <div>
                  <label className="text-sm text-gray-500 dark:text-gray-400">Server</label>
                  <p className="text-gray-900 dark:text-white">
                    {selectedAccount.incoming_server_type?.toUpperCase()} / {selectedAccount.incoming_host}
                  </p>
                </div>
              </div>
            </div>
          </section>

          {/* Signature */}
          <section className="mb-8">
            <h2 className="text-lg font-medium text-gray-900 dark:text-white mb-4">Signature</h2>
            <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
              <textarea
                value={signature}
                onChange={(e) => setSignature(e.target.value)}
                placeholder="Add a signature that will be appended to your emails..."
                rows={6}
                className="w-full px-3 py-2 bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg text-gray-900 dark:text-white focus:ring-2 focus:ring-brand-500"
              />
            </div>
          </section>

          {/* Sync */}
          <section className="mb-8">
            <h2 className="text-lg font-medium text-gray-900 dark:text-white mb-4">Sync</h2>
            <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
              <button
                onClick={handleSyncFolders}
                className="flex items-center gap-2 px-4 py-2 text-brand-600 border border-brand-600 rounded-lg hover:bg-brand-50 dark:hover:bg-brand-900/20"
              >
                <RefreshCw className="w-4 h-4" />
                Sync Folders
              </button>
            </div>
          </section>

          {/* Save button */}
          <div className="flex justify-end">
            <button
              onClick={handleSave}
              disabled={updateMutation.isPending}
              className="flex items-center gap-2 px-6 py-2 bg-brand-600 text-white rounded-lg hover:bg-brand-700 disabled:opacity-50"
            >
              {updateMutation.isPending ? (
                <Loader2 className="w-5 h-5 animate-spin" />
              ) : (
                <Save className="w-5 h-5" />
              )}
              Save Changes
            </button>
          </div>

          {updateMutation.isSuccess && (
            <p className="mt-4 text-sm text-green-600 dark:text-green-400 text-right">
              Settings saved successfully!
            </p>
          )}
        </>
      )}
    </div>
  )
}
