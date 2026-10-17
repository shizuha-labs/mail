import { useEffect, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useParams, useNavigate, useSearchParams } from 'react-router-dom'
import { messagesApi, mailApi } from '../services/api'
import { formatDistanceToNow, format } from 'date-fns'
import {
  ArrowLeft, Reply, ReplyAll, Forward, Trash2, Archive,
  Star, StarOff, Paperclip, Download, Loader2, Printer, MailOpen, Mail,
  ChevronDown, ChevronUp
} from 'lucide-react'
import clsx from 'clsx'
import toast from 'react-hot-toast'
import { useKeyboardShortcuts } from '../hooks/useKeyboardShortcuts'
import useTheme from '../hooks/useTheme'
import { useState } from 'react'

export default function MessagePage() {
  const { messageId } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [searchParams] = useSearchParams()
  const accountId = searchParams.get('account_id')
  const folderId = searchParams.get('folder')
  const [threadExpanded, setThreadExpanded] = useState({})
  const { isDark } = useTheme()

  const { data: message, isLoading } = useQuery({
    queryKey: ['message', messageId, accountId, folderId],
    queryFn: () => messagesApi.get(messageId, {
      ...(accountId ? { account_id: accountId } : {}),
      ...(folderId ? { folder: folderId } : {}),
    }).then(r => r.data),
  })

  // Fetch thread if message has thread_id
  // Note: JMAP thread IDs (short strings like "m") won't match local DB records,
  // so we silently ignore 404s and skip non-UUID thread IDs
  const isValidThreadId = message?.thread_id && message.thread_id.length > 8
  const { data: thread } = useQuery({
    queryKey: ['thread', message?.thread_id],
    queryFn: () => mailApi.getThread(message.thread_id).then(r => r.data).catch(() => null),
    enabled: !!isValidThreadId,
    retry: false,
  })

  const threadMessages = thread?.messages || []
  const hasThread = threadMessages.length > 1

  const trashMutation = useMutation({
    mutationFn: () => messagesApi.trash({ message_ids: [messageId], account_id: accountId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['messages'] })
      toast.success('Moved to trash')
      navigate(-1)
    },
  })

  const flagMutation = useMutation({
    mutationFn: (isFlagged) => messagesApi.flag({ message_ids: [messageId], is_flagged: isFlagged, account_id: accountId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['message', messageId] })
      queryClient.invalidateQueries({ queryKey: ['messages'] })
    },
  })

  const markReadMutation = useMutation({
    mutationFn: () => messagesApi.flag({ message_ids: [messageId], is_unread: false, account_id: accountId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['messages'] })
      queryClient.invalidateQueries({ queryKey: ['folders'] })
    },
  })

  // Auto-mark as read when message loads
  useEffect(() => {
    if (message && message.is_unread) {
      markReadMutation.mutate()
    }
  }, [message?.id])

  // Keyboard shortcuts for message view
  useKeyboardShortcuts({
    reply: () => navigate(`/compose?reply=${messageId}${folderId ? `&folder=${encodeURIComponent(folderId)}` : ''}`),
    replyAll: () => navigate(`/compose?replyAll=${messageId}${folderId ? `&folder=${encodeURIComponent(folderId)}` : ''}`),
    forward: () => navigate(`/compose?forward=${messageId}`),
    back: () => navigate(-1),
    trash: () => trashMutation.mutate(),
    print: () => handlePrint(),
  }, 'message')

  // Wrap email HTML with dark mode styles for iframe rendering
  const getEmailSrcDoc = (html) => {
    if (!isDark || !html) return html

    // Check if the email has its own background styling (rich HTML email)
    const hasBackground = /background(-color)?\s*[:=]/i.test(html) || /bgcolor/i.test(html)

    let darkCss
    if (hasBackground) {
      // Rich email with its own styling: dim brightness to reduce glare
      darkCss = `<style>
        html { background: #111827 !important; }
        body { filter: brightness(0.82) contrast(1.05); }
      </style>`
    } else {
      // Simple/plain email: apply full dark theme
      darkCss = `<style>
        html, body {
          background: #111827 !important;
          color: #d1d5db !important;
        }
        a { color: #93c5fd !important; }
        blockquote {
          border-left: 2px solid #4b5563 !important;
          color: #9ca3af !important;
        }
        hr { border-color: #374151 !important; }
        pre, code { background: #1f2937 !important; color: #e5e7eb !important; }
        table { color: inherit !important; }
      </style>`
    }

    if (html.includes('</head>')) {
      return html.replace('</head>', `${darkCss}</head>`)
    }
    if (html.includes('<body')) {
      return html.replace('<body', `${darkCss}<body`)
    }
    return `<html><head>${darkCss}</head><body>${html}</body></html>`
  }

  const handlePrint = () => {
    const printWindow = window.open('', '_blank')
    if (!printWindow) return
    const body = message.body_html || `<pre style="font-family:monospace;white-space:pre-wrap">${message.body_text || message.preview || ''}</pre>`
    printWindow.document.write(`
      <!DOCTYPE html>
      <html>
      <head>
        <title>${message.subject || 'Email'}</title>
        <style>
          body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 800px; margin: 0 auto; padding: 40px 20px; color: #333; }
          .header { border-bottom: 1px solid #e5e7eb; padding-bottom: 16px; margin-bottom: 24px; }
          .subject { font-size: 20px; font-weight: 600; margin-bottom: 12px; }
          .meta { font-size: 14px; color: #6b7280; line-height: 1.6; }
          .body { font-size: 14px; line-height: 1.6; }
          @media print { body { padding: 0; } }
        </style>
      </head>
      <body>
        <div class="header">
          <div class="subject">${message.subject || '(no subject)'}</div>
          <div class="meta">
            <strong>From:</strong> ${message.from_name ? `${message.from_name} &lt;${message.from_address}&gt;` : message.from_address}<br>
            <strong>To:</strong> ${message.to_addresses?.map(a => a.email || a).join(', ')}<br>
            ${message.cc_addresses?.length ? `<strong>Cc:</strong> ${message.cc_addresses.map(a => a.email || a).join(', ')}<br>` : ''}
            <strong>Date:</strong> ${message.date_received ? format(new Date(message.date_received), 'PPpp') : ''}
          </div>
        </div>
        <div class="body">${body}</div>
      </body>
      </html>
    `)
    printWindow.document.close()
    printWindow.print()
  }

  const toggleThreadMessage = (id) => {
    setThreadExpanded(prev => ({ ...prev, [id]: !prev[id] }))
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="w-8 h-8 animate-spin text-brand-600" />
      </div>
    )
  }

  if (!message) {
    return (
      <div className="flex items-center justify-center h-full text-gray-500">
        Message not found
      </div>
    )
  }

  // Render a single message (used for both standalone and thread messages)
  const renderMessageContent = (msg, isExpanded = true) => (
    <>
      {/* Message header */}
      <div className="p-6 border-b border-gray-200 dark:border-gray-800">
        <div className="flex items-start gap-4">
          <div className="w-10 h-10 rounded-full bg-brand-100 dark:bg-brand-900 flex items-center justify-center text-brand-600 font-medium flex-shrink-0">
            {(msg.from_name || msg.from_address || '?')[0].toUpperCase()}
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-medium text-gray-900 dark:text-white">
                {msg.from_name || msg.from_address}
              </span>
              {msg.from_name && (
                <span className="text-sm text-gray-500 dark:text-gray-400">
                  &lt;{msg.from_address}&gt;
                </span>
              )}
            </div>
            <div className="text-sm text-gray-500 dark:text-gray-400 mt-1">
              To: {msg.to_addresses?.map(a => a.email || a).join(', ')}
            </div>
            {msg.cc_addresses?.length > 0 && (
              <div className="text-sm text-gray-500 dark:text-gray-400">
                Cc: {msg.cc_addresses.map(a => a.email || a).join(', ')}
              </div>
            )}
          </div>
          <div className="text-sm text-gray-500 dark:text-gray-400 flex-shrink-0">
            {msg.date_received && format(new Date(msg.date_received), 'PPpp')}
          </div>
        </div>
      </div>

      {isExpanded && (
        <>
          {/* Attachments */}
          {msg.attachments?.length > 0 && (
            <div className="px-6 py-3 border-b border-gray-200 dark:border-gray-800">
              <div className="flex items-center gap-2 mb-2">
                <Paperclip className="w-4 h-4 text-gray-400" />
                <span className="text-sm text-gray-500 dark:text-gray-400">
                  {msg.attachments.length} attachment{msg.attachments.length > 1 ? 's' : ''}
                </span>
              </div>
              <div className="flex flex-wrap gap-2">
                {msg.attachments.map(attachment => (
                  <a
                    key={attachment.id}
                    href={`/api/attachments/${attachment.id}/download/`}
                    className="flex items-center gap-2 px-3 py-2 bg-gray-100 dark:bg-gray-800 rounded-lg text-sm hover:bg-gray-200 dark:hover:bg-gray-700"
                  >
                    <Download className="w-4 h-4" />
                    <span className="truncate max-w-[200px]">{attachment.filename}</span>
                    <span className="text-gray-400 text-xs">
                      ({Math.round(attachment.size_bytes / 1024)}KB)
                    </span>
                  </a>
                ))}
              </div>
            </div>
          )}

          {/* Message body */}
          <div className="bg-white dark:bg-gray-900">
            {msg.body_html ? (
              <iframe
                srcDoc={getEmailSrcDoc(msg.body_html)}
                sandbox="allow-same-origin"
                title="Email content"
                className="w-full border-0"
                style={{ minHeight: '200px' }}
                onLoad={(e) => {
                  const iframe = e.target
                  const doc = iframe.contentDocument
                  if (doc) {
                    const resize = () => {
                      const height = doc.documentElement.scrollHeight
                      iframe.style.height = height + 'px'
                    }
                    resize()
                    const observer = new MutationObserver(resize)
                    observer.observe(doc.body, { childList: true, subtree: true })
                    doc.querySelectorAll('img').forEach(img => {
                      img.addEventListener('load', resize)
                    })
                    // Open links in new tab for security
                    doc.querySelectorAll('a[href]').forEach(a => {
                      a.setAttribute('target', '_blank')
                      a.setAttribute('rel', 'noopener noreferrer')
                    })
                  }
                }}
              />
            ) : msg.body_text ? (
              <div className="p-6 text-gray-900 dark:text-gray-100 whitespace-pre-wrap font-mono text-sm">
                {msg.body_text}
              </div>
            ) : (
              <div className="p-6 text-gray-500 dark:text-gray-400">
                {msg.preview || '(No content)'}
              </div>
            )}
          </div>
        </>
      )}
    </>
  )

  return (
    <div className="h-full flex flex-col">
      {/* Toolbar */}
      <div className="flex items-center gap-2 p-4 border-b border-gray-200 dark:border-gray-800">
        <button
          onClick={() => navigate(-1)}
          className="p-2 text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800"
          title="Back (Esc)"
        >
          <ArrowLeft className="w-5 h-5" />
        </button>

        <div className="flex-1">
          <h1 className="text-lg font-semibold text-gray-900 dark:text-white truncate">
            {message.subject || '(no subject)'}
          </h1>
          {hasThread && (
            <span className="text-xs text-gray-500 dark:text-gray-400">
              {threadMessages.length} messages in conversation
            </span>
          )}
        </div>

        <button
          onClick={() => navigate(`/compose?reply=${messageId}${folderId ? `&folder=${encodeURIComponent(folderId)}` : ''}`)}
          className="p-2 text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800"
          title="Reply (r)"
        >
          <Reply className="w-5 h-5" />
        </button>
        <button
          onClick={() => navigate(`/compose?replyAll=${messageId}${folderId ? `&folder=${encodeURIComponent(folderId)}` : ''}`)}
          className="p-2 text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800"
          title="Reply All (a)"
        >
          <ReplyAll className="w-5 h-5" />
        </button>
        <button
          onClick={() => navigate(`/compose?forward=${messageId}`)}
          className="p-2 text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800"
          title="Forward (f)"
        >
          <Forward className="w-5 h-5" />
        </button>
        <button
          onClick={() => flagMutation.mutate(!message.is_flagged)}
          className={clsx(
            'p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800',
            message.is_flagged ? 'text-yellow-500' : 'text-gray-500 hover:text-gray-700'
          )}
          title={message.is_flagged ? 'Unflag' : 'Flag'}
        >
          {message.is_flagged ? <Star className="w-5 h-5 fill-current" /> : <StarOff className="w-5 h-5" />}
        </button>
        <button
          onClick={handlePrint}
          className="p-2 text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800"
          title="Print (p)"
        >
          <Printer className="w-5 h-5" />
        </button>
        <button
          onClick={() => trashMutation.mutate()}
          className="p-2 text-gray-500 hover:text-red-600 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800"
          title="Delete (#)"
        >
          <Trash2 className="w-5 h-5" />
        </button>
      </div>

      {/* Message content */}
      <div className="flex-1 overflow-y-auto">
        {hasThread ? (
          // Thread view: show all messages as collapsible accordion
          <div className="divide-y divide-gray-200 dark:divide-gray-800">
            {threadMessages.map((threadMsg, index) => {
              const isLast = index === threadMessages.length - 1
              const isCurrentMsg = threadMsg.id === messageId
              const isExpanded = isLast || isCurrentMsg || threadExpanded[threadMsg.id]

              return (
                <div key={threadMsg.id}>
                  {/* Collapsed header (click to expand) */}
                  {!isExpanded ? (
                    <button
                      onClick={() => toggleThreadMessage(threadMsg.id)}
                      className="w-full flex items-center gap-3 px-6 py-3 hover:bg-gray-50 dark:hover:bg-gray-800/50 text-left"
                    >
                      <div className="w-8 h-8 rounded-full bg-gray-200 dark:bg-gray-700 flex items-center justify-center text-gray-600 dark:text-gray-300 text-sm font-medium flex-shrink-0">
                        {(threadMsg.from_name || threadMsg.from_address || '?')[0].toUpperCase()}
                      </div>
                      <div className="flex-1 min-w-0">
                        <span className="text-sm font-medium text-gray-900 dark:text-white">
                          {threadMsg.from_name || threadMsg.from_address}
                        </span>
                        <span className="text-sm text-gray-500 dark:text-gray-400 ml-2 truncate">
                          {threadMsg.preview}
                        </span>
                      </div>
                      <span className="text-xs text-gray-400 flex-shrink-0">
                        {threadMsg.date_received && formatDistanceToNow(new Date(threadMsg.date_received), { addSuffix: true })}
                      </span>
                      <ChevronDown className="w-4 h-4 text-gray-400 flex-shrink-0" />
                    </button>
                  ) : (
                    <div>
                      {/* Expanded header with collapse button for non-last messages */}
                      {!isLast && (
                        <button
                          onClick={() => toggleThreadMessage(threadMsg.id)}
                          className="absolute right-6 mt-6 p-1 text-gray-400 hover:text-gray-600 z-10"
                        >
                          <ChevronUp className="w-4 h-4" />
                        </button>
                      )}
                      <div className="relative">
                        {renderMessageContent(threadMsg, true)}
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        ) : (
          // Single message view
          renderMessageContent(message)
        )}
      </div>
    </div>
  )
}
