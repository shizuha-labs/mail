import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Save, RefreshCw } from 'lucide-react'
import { mailApi } from '../services/api'
import { useMail } from '../contexts/MailContext'

export default function Settings() {
  const { selectedAccount: account, refetchAccounts } = useMail()
  const queryClient = useQueryClient()

  const [displayName, setDisplayName] = useState(account?.display_name || '')
  const [signature, setSignature] = useState(account?.signature || '')
  const [isSaving, setIsSaving] = useState(false)
  const [message, setMessage] = useState(null)

  const updateMutation = useMutation({
    mutationFn: (data) => mailApi.updateAccount(account?.id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['accounts'] })
      setMessage({ type: 'success', text: 'Settings saved successfully' })
    },
    onError: (error) => {
      setMessage({ type: 'error', text: error.message || 'Failed to save settings' })
    },
  })

  const handleSave = async () => {
    setIsSaving(true)
    setMessage(null)
    try {
      await updateMutation.mutateAsync({
        display_name: displayName,
        signature,
      })
    } finally {
      setIsSaving(false)
    }
  }

  const handleSyncFolders = async () => {
    try {
      await mailApi.syncFolders()
      queryClient.invalidateQueries({ queryKey: ['folders'] })
      setMessage({ type: 'success', text: 'Folders synced successfully' })
    } catch (error) {
      setMessage({ type: 'error', text: 'Failed to sync folders' })
    }
  }

  return (
    <div className="flex flex-col h-full overflow-y-auto">
      <div className="max-w-2xl mx-auto w-full p-6">
        <h1 className="text-2xl font-bold text-gray-900 mb-6">Settings</h1>

        {message && (
          <div
            className={`mb-6 p-4 rounded-lg ${
              message.type === 'success'
                ? 'bg-green-50 border border-green-200 text-green-700'
                : 'bg-red-50 border border-red-200 text-red-700'
            }`}
          >
            {message.text}
          </div>
        )}

        {/* Account Info */}
        <section className="bg-white rounded-lg border border-gray-200 p-6 mb-6">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">Account</h2>

          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Email Address
              </label>
              <input
                type="email"
                value={account?.email_address || ''}
                disabled
                className="w-full px-4 py-2 bg-gray-100 border border-gray-200 rounded-lg text-gray-500"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Display Name
              </label>
              <input
                type="text"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
              />
            </div>
          </div>
        </section>

        {/* Signature */}
        <section className="bg-white rounded-lg border border-gray-200 p-6 mb-6">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">Signature</h2>

          <textarea
            value={signature}
            onChange={(e) => setSignature(e.target.value)}
            rows={6}
            placeholder="Add your email signature here..."
            className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none resize-none"
          />
        </section>

        {/* Storage */}
        <section className="bg-white rounded-lg border border-gray-200 p-6 mb-6">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">Storage</h2>

          <div className="space-y-2">
            <div className="flex justify-between text-sm">
              <span className="text-gray-600">Used</span>
              <span className="font-medium">
                {account?.storage_used_mb || 0} MB / {account?.storage_quota_mb || 1024} MB
              </span>
            </div>
            <div className="w-full bg-gray-200 rounded-full h-2">
              <div
                className="bg-primary-600 h-2 rounded-full"
                style={{
                  width: `${Math.min(
                    100,
                    ((account?.storage_used_mb || 0) / (account?.storage_quota_mb || 1024)) * 100
                  )}%`,
                }}
              />
            </div>
          </div>
        </section>

        {/* Sync */}
        <section className="bg-white rounded-lg border border-gray-200 p-6 mb-6">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">Sync</h2>

          <button
            onClick={handleSyncFolders}
            className="flex items-center gap-2 px-4 py-2 text-primary-600 border border-primary-600 rounded-lg hover:bg-primary-50"
          >
            <RefreshCw className="w-4 h-4" />
            Sync Folders
          </button>
        </section>

        {/* Save Button */}
        <div className="flex justify-end">
          <button
            onClick={handleSave}
            disabled={isSaving}
            className="flex items-center gap-2 bg-primary-600 text-white px-6 py-2 rounded-lg hover:bg-primary-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Save className="w-4 h-4" />
            {isSaving ? 'Saving...' : 'Save Settings'}
          </button>
        </div>
      </div>
    </div>
  )
}
