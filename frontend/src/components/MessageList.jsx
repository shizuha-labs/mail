import { useState, useRef, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { formatDistanceToNow } from 'date-fns'
import {
  Paperclip, Star, StarOff, Trash2, Archive, CheckSquare, Square,
  MailOpen, Mail, ChevronDown, CheckCheck
} from 'lucide-react'
import { messagesApi } from '../services/api'
import clsx from 'clsx'
import toast from 'react-hot-toast'

export default function MessageList({ messages, onToggleFlag, onMarkReadUnread, folderId, folderRole, showAccount }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [selectedIds, setSelectedIds] = useState(new Set())
  const [showSelectFilter, setShowSelectFilter] = useState(false)
  const [focusedIndex, setFocusedIndex] = useState(-1)
  const selectFilterRef = useRef(null)

  // Close select filter dropdown on outside click
  useEffect(() => {
    const handleClick = (e) => {
      if (selectFilterRef.current && !selectFilterRef.current.contains(e.target)) {
        setShowSelectFilter(false)
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  const trashMutation = useMutation({
    mutationFn: (messageIds) => messagesApi.trash({
      message_ids: messageIds,
      // MAIL-143: role-based folders (sent/trash/...) have no folder UUID in
      // the route, so folderId is undefined there — send the role so the
      // backend moves the correct membership instead of silently falling back
      // to Inbox (which left a Sent message also in Trash).
      from_folder: folderId || folderRole
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['messages'] })
      queryClient.invalidateQueries({ queryKey: ['folders'] })
      setSelectedIds(new Set())
      toast.success('Moved to trash')
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (messageIds) => messagesApi.delete({
      message_ids: messageIds,
      folder_id: folderId || folderRole,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['messages'] })
      queryClient.invalidateQueries({ queryKey: ['folders'] })
      setSelectedIds(new Set())
      toast.success('Deleted permanently')
    },
  })

  const archiveMutation = useMutation({
    mutationFn: (messageIds) => messagesApi.move({
      message_ids: messageIds,
      folder_role: 'archive',
      from_folder: folderId || folderRole
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['messages'] })
      queryClient.invalidateQueries({ queryKey: ['folders'] })
      setSelectedIds(new Set())
      toast.success('Archived')
    },
  })

  const markReadUnreadMutation = useMutation({
    mutationFn: ({ messageIds, isUnread, accountId }) => messagesApi.flag({
      message_ids: messageIds,
      is_unread: isUnread,
      account_id: accountId,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['messages'] })
      queryClient.invalidateQueries({ queryKey: ['folders'] })
    },
  })

  const toggleSelect = (e, messageId) => {
    e.stopPropagation()
    const newSelected = new Set(selectedIds)
    if (newSelected.has(messageId)) {
      newSelected.delete(messageId)
    } else {
      newSelected.add(messageId)
    }
    setSelectedIds(newSelected)
  }

  const toggleSelectAll = () => {
    if (selectedIds.size === messages.length) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(messages.map(m => m.id)))
    }
  }

  const selectByFilter = (filter) => {
    let filtered
    switch (filter) {
      case 'all':
        filtered = messages.map(m => m.id)
        break
      case 'none':
        filtered = []
        break
      case 'read':
        filtered = messages.filter(m => !m.is_unread).map(m => m.id)
        break
      case 'unread':
        filtered = messages.filter(m => m.is_unread).map(m => m.id)
        break
      case 'starred':
        filtered = messages.filter(m => m.is_flagged).map(m => m.id)
        break
      default:
        filtered = []
    }
    setSelectedIds(new Set(filtered))
    setShowSelectFilter(false)
  }

  const handleMarkReadUnread = (e, message) => {
    e.stopPropagation()
    const newUnread = !message.is_unread
    markReadUnreadMutation.mutate({
      messageIds: [message.id],
      isUnread: newUnread,
      accountId: message.account_id,
    })
  }

  const handleBulkMarkRead = () => {
    const ids = [...selectedIds]
    markReadUnreadMutation.mutate({
      messageIds: ids,
      isUnread: false,
    })
    setSelectedIds(new Set())
    toast.success('Marked as read')
  }

  const handleBulkMarkUnread = () => {
    const ids = [...selectedIds]
    markReadUnreadMutation.mutate({
      messageIds: ids,
      isUnread: true,
    })
    setSelectedIds(new Set())
    toast.success('Marked as unread')
  }

  const handleMarkAllRead = () => {
    const unreadIds = messages.filter(m => m.is_unread).map(m => m.id)
    if (unreadIds.length === 0) return
    markReadUnreadMutation.mutate({
      messageIds: unreadIds,
      isUnread: false,
    })
    toast.success(`Marked ${unreadIds.length} messages as read`)
  }

  // Expose focusedIndex for keyboard navigation
  const getFocusedMessage = useCallback(() => {
    if (focusedIndex >= 0 && focusedIndex < messages.length) {
      return messages[focusedIndex]
    }
    return null
  }, [focusedIndex, messages])

  if (!messages?.length) {
    return (
      <div className="flex items-center justify-center h-64 text-gray-500 dark:text-gray-400">
        No messages
      </div>
    )
  }

  return (
    <div>
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-4 py-2 border-b border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900">
        {/* Select all checkbox with filter dropdown */}
        <div className="relative flex items-center" ref={selectFilterRef}>
          <button
            onClick={toggleSelectAll}
            className="p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
          >
            {selectedIds.size === messages.length && messages.length > 0 ? (
              <CheckSquare className="w-5 h-5 text-brand-600 dark:text-brand-400" />
            ) : (
              <Square className="w-5 h-5" />
            )}
          </button>
          <button
            onClick={() => setShowSelectFilter(!showSelectFilter)}
            className="p-0.5 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
          >
            <ChevronDown className="w-4 h-4" />
          </button>

          {showSelectFilter && (
            <div className="absolute top-full left-0 mt-1 w-36 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg z-50 py-1">
              {[
                { key: 'all', label: 'All' },
                { key: 'none', label: 'None' },
                { key: 'read', label: 'Read' },
                { key: 'unread', label: 'Unread' },
                { key: 'starred', label: 'Starred' },
              ].map(({ key, label }) => (
                <button
                  key={key}
                  onClick={() => selectByFilter(key)}
                  className="w-full px-3 py-1.5 text-left text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
                >
                  {label}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Bulk actions (when items selected) */}
        {selectedIds.size > 0 ? (
          <>
            <span className="text-sm font-medium text-brand-700 dark:text-brand-300 ml-1">
              {selectedIds.size} selected
            </span>
            <div className="flex-1" />
            <button
              onClick={handleBulkMarkRead}
              className="flex items-center gap-1 px-2.5 py-1.5 text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg"
              title="Mark as read"
            >
              <MailOpen className="w-4 h-4" />
              <span className="hidden sm:inline">Read</span>
            </button>
            <button
              onClick={handleBulkMarkUnread}
              className="flex items-center gap-1 px-2.5 py-1.5 text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg"
              title="Mark as unread"
            >
              <Mail className="w-4 h-4" />
              <span className="hidden sm:inline">Unread</span>
            </button>
            <button
              onClick={() => archiveMutation.mutate([...selectedIds])}
              disabled={archiveMutation.isPending}
              className="flex items-center gap-1 px-2.5 py-1.5 text-sm font-medium text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg"
            >
              <Archive className="w-4 h-4" />
              <span className="hidden sm:inline">Archive</span>
            </button>
            <button
              onClick={() => (folderRole === 'trash'
                ? deleteMutation.mutate([...selectedIds])
                : trashMutation.mutate([...selectedIds]))}
              disabled={trashMutation.isPending || deleteMutation.isPending}
              className="flex items-center gap-1 px-2.5 py-1.5 text-sm font-medium text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg"
            >
              <Trash2 className="w-4 h-4" />
              <span className="hidden sm:inline">{folderRole === 'trash' ? 'Delete permanently' : 'Delete'}</span>
            </button>
          </>
        ) : (
          <>
            <div className="flex-1" />
            {messages.some(m => m.is_unread) && (
              <button
                onClick={handleMarkAllRead}
                className="flex items-center gap-1 px-2.5 py-1.5 text-sm text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg"
                title="Mark all as read"
              >
                <CheckCheck className="w-4 h-4" />
                <span className="hidden sm:inline">Mark all read</span>
              </button>
            )}
          </>
        )}
      </div>

      {/* Message list */}
      <div className="divide-y divide-gray-200 dark:divide-gray-800">
        {messages.map((message, index) => (
          <div
            key={message.id}
            onClick={() => {
              const params = new URLSearchParams()
              if (message.account_id) params.set('account_id', message.account_id)
              if (message.folder_id) params.set('folder', message.folder_id)
              navigate(`/message/${message.id}${params.size ? `?${params.toString()}` : ''}`)
            }}
            className={clsx(
              'flex items-start gap-3 px-4 py-3 cursor-pointer transition-colors',
              selectedIds.has(message.id)
                ? 'bg-brand-50 dark:bg-brand-900/20'
                : focusedIndex === index
                  ? 'bg-blue-50 dark:bg-blue-900/20'
                  : message.is_unread
                    ? 'bg-white dark:bg-gray-900 hover:bg-gray-50 dark:hover:bg-gray-800/50'
                    : 'bg-gray-50/50 dark:bg-gray-900/50 hover:bg-gray-100 dark:hover:bg-gray-800/50'
            )}
            data-message-index={index}
          >
            {/* Checkbox */}
            <button
              onClick={(e) => toggleSelect(e, message.id)}
              className="flex-shrink-0 p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
            >
              {selectedIds.has(message.id) ? (
                <CheckSquare className="w-5 h-5 text-brand-600 dark:text-brand-400" />
              ) : (
                <Square className="w-5 h-5" />
              )}
            </button>

            {/* Flag button */}
            <button
              onClick={(e) => {
                e.stopPropagation()
                onToggleFlag?.(message.id, !message.is_flagged, message.account_id)
              }}
              className={clsx(
                'flex-shrink-0 p-1 rounded',
                message.is_flagged
                  ? 'text-yellow-500 hover:text-yellow-600'
                  : 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-300'
              )}
            >
              {message.is_flagged ? <Star className="w-5 h-5 fill-current" /> : <StarOff className="w-5 h-5" />}
            </button>

            {/* Read/unread toggle */}
            <button
              onClick={(e) => handleMarkReadUnread(e, message)}
              className={clsx(
                'flex-shrink-0 p-1 rounded',
                message.is_unread
                  ? 'text-brand-600 dark:text-brand-400 hover:text-brand-700'
                  : 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-300'
              )}
              title={message.is_unread ? 'Mark as read' : 'Mark as unread'}
            >
              {message.is_unread ? <Mail className="w-4 h-4" /> : <MailOpen className="w-4 h-4" />}
            </button>

            {/* Content */}
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className={clsx(
                  'text-sm truncate',
                  message.is_unread ? 'font-semibold text-gray-900 dark:text-white' : 'text-gray-600 dark:text-gray-300'
                )}>
                  {message.from_name || message.from_address}
                </span>
                {showAccount && message.account_email && (
                  <span className="text-xs px-1.5 py-0.5 bg-gray-200 dark:bg-gray-700 text-gray-500 dark:text-gray-400 rounded flex-shrink-0">
                    {message.account_email.split('@')[0]}
                  </span>
                )}
                {message.has_attachments && (
                  <Paperclip className="w-4 h-4 text-gray-400 flex-shrink-0" />
                )}
              </div>
              <div className={clsx(
                'text-sm truncate',
                message.is_unread ? 'font-medium text-gray-900 dark:text-white' : 'text-gray-600 dark:text-gray-300'
              )}>
                {message.subject || '(no subject)'}
              </div>
              <div className="text-sm text-gray-500 dark:text-gray-400 truncate">
                {message.preview}
              </div>
            </div>

            {/* Date */}
            <div className="flex-shrink-0 text-xs text-gray-500 dark:text-gray-400">
              {message.date_received && formatDistanceToNow(new Date(message.date_received), { addSuffix: true })}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
