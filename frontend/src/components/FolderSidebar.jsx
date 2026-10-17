import { useState } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import {
  Inbox,
  Send,
  FileText,
  Trash2,
  AlertCircle,
  Archive,
  Folder,
  ChevronRight,
  ChevronDown,
  Plus,
  Settings,
  Mail,
} from 'lucide-react'
import { useMail } from '../contexts/MailContext'
import { cn } from '../utils/cn'

const folderIcons = {
  inbox: Inbox,
  sent: Send,
  drafts: FileText,
  trash: Trash2,
  junk: AlertCircle,
  archive: Archive,
  custom: Folder,
}

const folderRoutes = {
  inbox: 'inbox',
  sent: 'sent',
  drafts: 'drafts',
  trash: 'trash',
  junk: 'junk',
  archive: 'archive',
}

export default function FolderSidebar({ collapsed }) {
  const { accounts, selectedAccount, switchAccount, folders, setSelectedFolder, hasAccounts } = useMail()
  const navigate = useNavigate()
  const location = useLocation()
  const [accountDropdownOpen, setAccountDropdownOpen] = useState(false)

  const handleFolderClick = (folder) => {
    setSelectedFolder(folder)
    const route = folderRoutes[folder.role] || `folder/${folder.id}`
    navigate(`/${route}`)
  }

  const handleAccountSwitch = (account) => {
    switchAccount(account.id)
    setAccountDropdownOpen(false)
    navigate('/inbox')
  }

  // Sort folders: system folders first, then custom
  const sortedFolders = [...(folders || [])].sort((a, b) => {
    const order = ['inbox', 'sent', 'drafts', 'archive', 'junk', 'trash', 'custom']
    return order.indexOf(a.role) - order.indexOf(b.role)
  })

  const isActive = (folder) => {
    const route = folderRoutes[folder.role] || `folder/${folder.id}`
    return location.pathname === `/${route}`
  }

  return (
    <div className="flex flex-col h-full">
      {/* Account Switcher */}
      {!collapsed && (
        <div className="p-3 border-b border-gray-200 dark:border-gray-800">
          {hasAccounts ? (
            <div className="relative">
              <button
                onClick={() => setAccountDropdownOpen(!accountDropdownOpen)}
                className="w-full flex items-center gap-3 p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
              >
                <div className="w-8 h-8 rounded-full bg-brand-100 dark:bg-brand-900/30 flex items-center justify-center flex-shrink-0">
                  <Mail className="w-4 h-4 text-brand-600" />
                </div>
                <div className="flex-1 text-left min-w-0">
                  <div className="text-sm font-medium text-gray-900 dark:text-white truncate">
                    {selectedAccount?.display_name || selectedAccount?.email_address?.split('@')[0]}
                  </div>
                  <div className="text-xs text-gray-500 dark:text-gray-400 truncate">
                    {selectedAccount?.email_address}
                  </div>
                </div>
                <ChevronDown className={cn(
                  'w-4 h-4 text-gray-400 transition-transform',
                  accountDropdownOpen && 'rotate-180'
                )} />
              </button>

              {/* Dropdown */}
              {accountDropdownOpen && (
                <div className="absolute top-full left-0 right-0 mt-1 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg z-50 overflow-hidden">
                  {accounts?.map((account) => (
                    <button
                      key={account.id}
                      onClick={() => handleAccountSwitch(account)}
                      className={cn(
                        'w-full flex items-center gap-3 p-3 text-left hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors',
                        account.id === selectedAccount?.id && 'bg-brand-50 dark:bg-brand-900/20'
                      )}
                    >
                      <div className="w-8 h-8 rounded-full bg-brand-100 dark:bg-brand-900/30 flex items-center justify-center flex-shrink-0">
                        <Mail className="w-4 h-4 text-brand-600" />
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="text-sm font-medium text-gray-900 dark:text-white truncate">
                          {account.display_name || account.email_address.split('@')[0]}
                        </div>
                        <div className="text-xs text-gray-500 dark:text-gray-400 truncate">
                          {account.email_address}
                        </div>
                      </div>
                      {account.unread_count > 0 && (
                        <span className="px-2 py-0.5 text-xs font-medium bg-brand-100 text-brand-700 dark:bg-brand-900/30 dark:text-brand-400 rounded-full">
                          {account.unread_count}
                        </span>
                      )}
                    </button>
                  ))}

                  <div className="border-t border-gray-200 dark:border-gray-700">
                    <button
                      onClick={() => {
                        setAccountDropdownOpen(false)
                        navigate('/accounts/add')
                      }}
                      className="w-full flex items-center gap-3 p-3 text-left hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors text-gray-600 dark:text-gray-400"
                    >
                      <Plus className="w-5 h-5" />
                      <span className="text-sm">Add Account</span>
                    </button>
                    <button
                      onClick={() => {
                        setAccountDropdownOpen(false)
                        navigate('/accounts')
                      }}
                      className="w-full flex items-center gap-3 p-3 text-left hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors text-gray-600 dark:text-gray-400"
                    >
                      <Settings className="w-5 h-5" />
                      <span className="text-sm">Manage Accounts</span>
                    </button>
                  </div>
                </div>
              )}
            </div>
          ) : (
            <button
              onClick={() => navigate('/accounts/add')}
              className="w-full flex items-center gap-3 p-2 rounded-lg border-2 border-dashed border-gray-300 dark:border-gray-700 hover:border-brand-500 hover:bg-brand-50 dark:hover:bg-brand-900/20 transition-colors"
            >
              <Plus className="w-5 h-5 text-gray-400" />
              <span className="text-sm text-gray-600 dark:text-gray-400">Add Email Account</span>
            </button>
          )}
        </div>
      )}

      {/* Folders */}
      <nav className="flex-1 overflow-y-auto py-2">
        {sortedFolders.map((folder) => {
          const Icon = folderIcons[folder.role] || Folder
          const active = isActive(folder)

          return (
            <button
              key={folder.id}
              onClick={() => handleFolderClick(folder)}
              className={cn(
                'w-full flex items-center gap-3 px-4 py-2 text-left transition-colors',
                active
                  ? 'bg-primary-50 text-primary-700 border-r-2 border-primary-600'
                  : 'text-gray-700 hover:bg-gray-100'
              )}
            >
              <Icon className={cn('w-5 h-5 flex-shrink-0', active && 'text-primary-600')} />
              {!collapsed && (
                <>
                  <span className="flex-1 truncate">{folder.name}</span>
                  {folder.unread_messages > 0 && (
                    <span
                      className={cn(
                        'px-2 py-0.5 text-xs font-medium rounded-full',
                        active
                          ? 'bg-primary-100 text-primary-700'
                          : 'bg-gray-200 text-gray-600'
                      )}
                    >
                      {folder.unread_messages}
                    </span>
                  )}
                </>
              )}
            </button>
          )
        })}

        {/* Custom folders divider */}
        {sortedFolders.some((f) => f.role === 'custom') && !collapsed && (
          <div className="px-4 py-2 mt-2">
            <div className="flex items-center gap-2 text-xs text-gray-500 font-medium uppercase">
              <ChevronRight className="w-3 h-3" />
              <span>Labels</span>
            </div>
          </div>
        )}
      </nav>

      {/* Bottom actions */}
      {!collapsed && (
        <div className="p-3 border-t border-gray-200 dark:border-gray-800">
          <button
            onClick={() => navigate('/accounts')}
            className="w-full flex items-center gap-3 px-4 py-2 text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition-colors"
          >
            <Settings className="w-5 h-5" />
            <span className="text-sm">Manage Accounts</span>
          </button>
        </div>
      )}
    </div>
  )
}
