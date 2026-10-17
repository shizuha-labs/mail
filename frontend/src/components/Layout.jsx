import { Outlet, NavLink, useNavigate, useLocation } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'
import { useMail } from '../contexts/MailContext'
import {
  Inbox, Send, FileEdit, Trash2, FolderOpen, Settings,
  PenSquare, Search, Menu, X, LogOut, Mail, Plus, Users, Loader2,
  Sun, Moon, Keyboard
} from 'lucide-react'
import { useState, useRef } from 'react'
import clsx from 'clsx'
import useTheme from '../hooks/useTheme'
import { useKeyboardShortcuts } from '../hooks/useKeyboardShortcuts'
import KeyboardShortcutsHelp from './KeyboardShortcutsHelp'

export default function Layout() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [showShortcuts, setShowShortcuts] = useState(false)
  const { isDark, toggleTheme } = useTheme()
  const searchInputRef = useRef(null)

  const {
    accounts,
    accountsLoading,
    selectedAccount,
    switchAccount,
    hasAccounts,
    folders,
    accountUnread,
  } = useMail()

  // Global keyboard shortcuts
  useKeyboardShortcuts({
    compose: () => navigate('/compose'),
    search: () => searchInputRef.current?.focus(),
    help: () => setShowShortcuts(prev => !prev),
  }, 'global')

  // Allow account management routes through even when no accounts exist
  const isAccountRoute = location.pathname.startsWith('/accounts')

  const handleSearch = (e) => {
    e.preventDefault()
    if (searchQuery.trim()) {
      navigate(`/inbox?search=${encodeURIComponent(searchQuery)}`)
    }
  }

  // Show loading while accounts are being fetched
  if (accountsLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-950">
        <div className="text-center">
          <Loader2 className="w-8 h-8 animate-spin text-brand-600 mx-auto" />
          <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">Loading mail...</p>
        </div>
      </div>
    )
  }

  // No accounts configured and not on an account management route - show welcome/setup screen
  if (!hasAccounts && !isAccountRoute) {
    return (
      <div className="min-h-screen bg-gray-50 dark:bg-gray-950 flex items-center justify-center">
        <div className="text-center p-8 max-w-lg">
          <Mail className="w-20 h-20 mx-auto text-brand-600 mb-6" />
          <h1 className="text-3xl font-bold text-gray-900 dark:text-white mb-3">
            Welcome to Shizuha Mail
          </h1>
          <p className="text-gray-600 dark:text-gray-400 mb-8 text-lg">
            Connect your email accounts to start sending and receiving emails.
            We support Gmail, Outlook, Yahoo, Stalwart, and any IMAP/SMTP provider.
          </p>
          <button
            onClick={() => navigate('/accounts/add')}
            className="inline-flex items-center gap-2 px-8 py-3 bg-brand-600 text-white rounded-lg hover:bg-brand-700 text-lg font-medium"
          >
            <Plus className="w-6 h-6" />
            Add Email Account
          </button>
          <div className="mt-4">
            <button
              onClick={logout}
              className="text-sm text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
            >
              Sign out
            </button>
          </div>
        </div>
      </div>
    )
  }

  const inboxUnread = folders?.find(f => f.role === 'inbox')?.unread_messages || 0

  const navItems = [
    { to: '/inbox', icon: Inbox, label: 'Inbox', badge: inboxUnread },
    { to: '/sent', icon: Send, label: 'Sent' },
    { to: '/drafts', icon: FileEdit, label: 'Drafts' },
    { to: '/trash', icon: Trash2, label: 'Trash' },
  ]

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-950 flex">
      {/* Keyboard shortcuts help */}
      {showShortcuts && <KeyboardShortcutsHelp onClose={() => setShowShortcuts(false)} />}

      {/* Mobile sidebar backdrop */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-20 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside className={clsx(
        'fixed inset-y-0 left-0 z-30 w-64 bg-white dark:bg-gray-900 border-r border-gray-200 dark:border-gray-800 transform transition-transform lg:translate-x-0 lg:static',
        sidebarOpen ? 'translate-x-0' : '-translate-x-full'
      )}>
        <div className="flex flex-col h-full">
          {/* Logo */}
          <div className="h-16 flex items-center justify-between px-4 border-b border-gray-200 dark:border-gray-800">
            <div className="flex items-center gap-2">
              <Mail className="w-6 h-6 text-brand-600" />
              <span className="text-lg font-semibold text-gray-900 dark:text-white">Shizuha Mail</span>
            </div>
            <button
              onClick={() => setSidebarOpen(false)}
              className="lg:hidden p-2 text-gray-500 hover:text-gray-700"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* Account selector (when multiple accounts) */}
          {accounts && accounts.length > 0 && (
            <div className="px-4 pt-3">
              <select
                value={selectedAccount?.id || 'all'}
                onChange={(e) => switchAccount(e.target.value)}
                className="w-full px-3 py-1.5 text-sm bg-gray-100 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg text-gray-900 dark:text-white"
              >
                {accounts.length > 1 && (
                  <option value="all">All Accounts</option>
                )}
                {accounts.map((acc) => (
                  <option key={acc.id} value={acc.id}>
                    {acc.display_name || acc.email_address}
                  </option>
                ))}
              </select>
            </div>
          )}

          {/* Compose button */}
          <div className="p-4">
            <button
              onClick={() => navigate('/compose')}
              className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-brand-600 text-white rounded-lg hover:bg-brand-700 transition-colors"
            >
              <PenSquare className="w-5 h-5" />
              Compose
            </button>
          </div>

          {/* Navigation */}
          <nav className="flex-1 overflow-y-auto px-3">
            <ul className="space-y-1">
              {navItems.map(({ to, icon: Icon, label, badge }) => (
                <li key={to}>
                  <NavLink
                    to={to}
                    onClick={() => setSidebarOpen(false)}
                    className={({ isActive }) => clsx(
                      'flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors',
                      isActive
                        ? 'bg-brand-100 text-brand-800 dark:bg-brand-500/20 dark:text-brand-300'
                        : 'text-gray-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800'
                    )}
                  >
                    <Icon className="w-5 h-5" />
                    <span className="flex-1">{label}</span>
                    {badge > 0 && (
                      <span className="px-2 py-0.5 text-xs font-medium bg-brand-600 text-white rounded-full">
                        {badge}
                      </span>
                    )}
                  </NavLink>
                </li>
              ))}
            </ul>

            {/* Custom folders */}
            {folders?.length > 0 && (
              <div className="mt-6">
                <h3 className="px-3 text-xs font-semibold text-gray-500 uppercase tracking-wider">
                  Folders
                </h3>
                <ul className="mt-2 space-y-1">
                  {folders.filter(f => !['inbox', 'sent', 'drafts', 'trash', 'junk', 'archive'].includes(f.role)).map(folder => (
                    <li key={folder.id}>
                      <NavLink
                        to={`/folder/${folder.id}`}
                        onClick={() => setSidebarOpen(false)}
                        className={({ isActive }) => clsx(
                          'flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors',
                          isActive
                            ? 'bg-brand-100 text-brand-800 dark:bg-brand-500/20 dark:text-brand-300'
                            : 'text-gray-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800'
                        )}
                      >
                        <FolderOpen className="w-5 h-5" />
                        <span className="flex-1">{folder.name}</span>
                        {folder.unread_messages > 0 && (
                          <span className="text-xs text-gray-500">{folder.unread_messages}</span>
                        )}
                      </NavLink>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </nav>

          {/* Settings & user */}
          <div className="p-4 border-t border-gray-200 dark:border-gray-800">
            <NavLink
              to="/accounts"
              onClick={() => setSidebarOpen(false)}
              className="flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800"
            >
              <Users className="w-5 h-5" />
              Accounts
            </NavLink>
            <NavLink
              to="/settings"
              onClick={() => setSidebarOpen(false)}
              className="flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800"
            >
              <Settings className="w-5 h-5" />
              Settings
            </NavLink>
            <button
              onClick={() => setShowShortcuts(true)}
              className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800"
            >
              <Keyboard className="w-5 h-5" />
              Shortcuts
              <span className="ml-auto text-xs text-gray-400">?</span>
            </button>
            <div className="mt-2 flex items-center gap-3 px-3 py-2">
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-gray-900 dark:text-white truncate">
                  {user?.email}
                </p>
                <p className="text-xs text-gray-500 dark:text-gray-400 truncate">
                  {selectedAccount?.email_address}
                </p>
              </div>
              <button
                onClick={logout}
                className="p-2 text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
                title="Sign out"
              >
                <LogOut className="w-5 h-5" />
              </button>
            </div>
          </div>
        </div>
      </aside>

      {/* Main content */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Header */}
        <header className="h-16 flex items-center gap-4 px-4 bg-white dark:bg-gray-900 border-b border-gray-200 dark:border-gray-800">
          <button
            onClick={() => setSidebarOpen(true)}
            className="lg:hidden p-2 text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
          >
            <Menu className="w-6 h-6" />
          </button>

          <form onSubmit={handleSearch} className="flex-1 max-w-xl">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
              <input
                ref={searchInputRef}
                type="search"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search mail... (from: to: is:unread has:attachment)"
                className="w-full pl-10 pr-4 py-2 bg-gray-100 dark:bg-gray-800 border-none rounded-lg text-sm focus:ring-2 focus:ring-brand-500"
              />
            </div>
          </form>

          <button
            onClick={toggleTheme}
            className="p-2 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800"
            title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
          >
            {isDark ? <Sun className="w-5 h-5" /> : <Moon className="w-5 h-5" />}
          </button>
        </header>

        {/* Page content */}
        <main className="flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
