import { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { sendApi, messagesApi, contactsApi } from '../services/api'
import { useMail } from '../contexts/MailContext'
import { Send, X, Paperclip, Loader2, ChevronDown, Mail } from 'lucide-react'
import toast from 'react-hot-toast'
import ContactInput from '../components/ContactInput'

export default function ComposePage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [searchParams] = useSearchParams()
  const { accounts, selectedAccount } = useMail()
  const fileInputRef = useRef(null)
  const composeRef = useRef(null)

  const replyTo = searchParams.get('reply')
  const replyAllTo = searchParams.get('replyAll')
  const forwardFrom = searchParams.get('forward')
  const sourceFolder = searchParams.get('folder')

  // Form state
  const [fromAccountId, setFromAccountId] = useState(selectedAccount?.id || '')
  const [toList, setToList] = useState([])
  const [ccList, setCcList] = useState([])
  const [bccList, setBccList] = useState([])
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const [attachments, setAttachments] = useState([])
  const [showCc, setShowCc] = useState(false)
  const [showBcc, setShowBcc] = useState(false)
  const [showFromDropdown, setShowFromDropdown] = useState(false)
  const [isDragOver, setIsDragOver] = useState(false)
  const [undoTimer, setUndoTimer] = useState(null)
  const [isSending, setIsSending] = useState(false)

  // Update fromAccountId when selectedAccount changes
  useEffect(() => {
    if (selectedAccount && !fromAccountId) {
      setFromAccountId(selectedAccount.id)
    }
  }, [selectedAccount, fromAccountId])

  // Get the selected "from" account
  const fromAccount = accounts?.find(a => a.id === fromAccountId) || selectedAccount

  // Auto-insert signature for new compose (not reply/forward)
  useEffect(() => {
    if (!replyTo && !replyAllTo && !forwardFrom && fromAccount?.signature && !body) {
      setBody(`\n\n-- \n${fromAccount.signature}`)
    }
  }, [fromAccount?.id])

  // Load original message for reply/forward
  const { data: originalMessage } = useQuery({
    queryKey: ['message', replyTo || replyAllTo || forwardFrom, sourceFolder],
    queryFn: () => messagesApi.get(
      replyTo || replyAllTo || forwardFrom,
      sourceFolder ? { folder: sourceFolder } : {},
    ).then(r => r.data),
    enabled: !!(replyTo || replyAllTo || forwardFrom),
  })

  // Apply original message data when loaded
  useEffect(() => {
    if (originalMessage) {
      if (replyTo || replyAllTo) {
        setToList([originalMessage.from_address])
        setSubject(`Re: ${originalMessage.subject}`)
        if (replyAllTo) {
          const ccEmails = originalMessage.to_addresses
            ?.map(a => a.email || a)
            .filter(a => a !== originalMessage.from_address) || []
          setCcList(ccEmails)
          if (ccEmails.length > 0) setShowCc(true)
        }
        // Insert signature before quoted text for replies
        const sig = fromAccount?.signature ? `\n\n-- \n${fromAccount.signature}\n\n` : '\n\n'
        setBody(`${sig}On ${originalMessage.date_sent || 'unknown date'}, ${originalMessage.from_address} wrote:\n> ${(originalMessage.body_text || originalMessage.preview || '').split('\n').join('\n> ')}`)
        // Use the same account the message was received on
        if (originalMessage.account_id) {
          setFromAccountId(originalMessage.account_id)
        }
      } else if (forwardFrom) {
        setSubject(`Fwd: ${originalMessage.subject}`)
        const sig = fromAccount?.signature ? `\n\n-- \n${fromAccount.signature}\n\n` : '\n\n'
        setBody(`${sig}---------- Forwarded message ----------\nFrom: ${originalMessage.from_address}\nSubject: ${originalMessage.subject}\n\n${originalMessage.body_text || originalMessage.preview || ''}`)
        if (originalMessage.account_id) {
          setFromAccountId(originalMessage.account_id)
        }
      }
    }
  }, [originalMessage, replyTo, replyAllTo, forwardFrom])

  const sendMutation = useMutation({
    mutationFn: (data) => sendApi.send(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['messages'] })
      queryClient.invalidateQueries({ queryKey: ['folders'] })
      toast.success('Message sent')
      navigate('/sent')
    },
    onError: (error) => {
      toast.error(error.response?.data?.detail || 'Failed to send message')
    },
  })

  const processFiles = useCallback((files) => {
    Array.from(files).forEach(file => {
      const reader = new FileReader()
      reader.onload = () => {
        setAttachments(prev => [...prev, {
          filename: file.name,
          content: reader.result,
          content_type: file.type || 'application/octet-stream',
          size: file.size,
        }])
      }
      reader.readAsDataURL(file)
    })
  }, [])

  const handleFileSelect = (e) => {
    processFiles(e.target.files)
    e.target.value = ''
  }

  const removeAttachment = (index) => {
    setAttachments(prev => prev.filter((_, i) => i !== index))
  }

  const formatFileSize = (bytes) => {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  }

  // Drag and drop handlers
  const handleDragOver = (e) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragOver(true)
  }

  const handleDragLeave = (e) => {
    e.preventDefault()
    e.stopPropagation()
    // Only set false if leaving the compose container
    if (composeRef.current && !composeRef.current.contains(e.relatedTarget)) {
      setIsDragOver(false)
    }
  }

  const handleDrop = (e) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragOver(false)
    if (e.dataTransfer.files?.length > 0) {
      processFiles(e.dataTransfer.files)
    }
  }

  const doSend = () => {
    sendMutation.mutate({
      account_id: fromAccountId,
      to_addresses: toList,
      cc_addresses: ccList,
      bcc_addresses: bccList,
      subject,
      body_text: body,
      reply_to_message_id: replyTo || replyAllTo || null,
      reply_to_folder_id: (replyTo || replyAllTo) ? sourceFolder : null,
      attachments: attachments.map(a => ({
        filename: a.filename,
        content: a.content,
        content_type: a.content_type,
      })),
    })
  }

  const handleSend = () => {
    if (toList.length === 0) {
      toast.error('Please enter at least one recipient')
      return
    }

    // Undo send: delay actual send by 5 seconds
    setIsSending(true)
    const timer = setTimeout(() => {
      setUndoTimer(null)
      setIsSending(false)
      doSend()
    }, 5000)

    setUndoTimer(timer)

    toast((t) => (
      <div className="flex items-center gap-3">
        <span>Sending message...</span>
        <button
          onClick={() => {
            clearTimeout(timer)
            setUndoTimer(null)
            setIsSending(false)
            toast.dismiss(t.id)
            toast('Send cancelled', { icon: '↩' })
          }}
          className="px-2 py-1 text-sm font-medium text-brand-600 hover:text-brand-700 bg-brand-50 rounded"
        >
          Undo
        </button>
      </div>
    ), { duration: 5000, icon: '📤' })
  }

  // Cancel undo timer on unmount
  useEffect(() => {
    return () => {
      if (undoTimer) clearTimeout(undoTimer)
    }
  }, [undoTimer])

  return (
    <div
      ref={composeRef}
      className="h-full flex flex-col bg-white dark:bg-gray-900 relative"
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {/* Drag overlay */}
      {isDragOver && (
        <div className="absolute inset-0 z-50 bg-brand-500/10 border-2 border-dashed border-brand-500 rounded-lg flex items-center justify-center pointer-events-none">
          <div className="text-center">
            <Paperclip className="w-12 h-12 mx-auto text-brand-500 mb-2" />
            <p className="text-lg font-medium text-brand-700 dark:text-brand-300">Drop files to attach</p>
          </div>
        </div>
      )}

      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b border-gray-200 dark:border-gray-800">
        <h1 className="text-lg font-semibold text-gray-900 dark:text-white">
          {replyTo ? 'Reply' : replyAllTo ? 'Reply All' : forwardFrom ? 'Forward' : 'New Message'}
        </h1>
        <div className="flex items-center gap-2">
          <button
            onClick={() => navigate(-1)}
            className="px-4 py-2 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg"
          >
            <X className="w-5 h-5" />
          </button>
          <button
            onClick={handleSend}
            disabled={isSending || sendMutation.isPending}
            className="flex items-center gap-2 px-4 py-2 bg-brand-600 text-white rounded-lg hover:bg-brand-700 disabled:opacity-50"
          >
            {(isSending || sendMutation.isPending) ? (
              <Loader2 className="w-5 h-5 animate-spin" />
            ) : (
              <Send className="w-5 h-5" />
            )}
            Send
          </button>
        </div>
      </div>

      {/* Form */}
      <div className="flex-1 flex flex-col overflow-hidden">
        <div className="p-4 space-y-3 border-b border-gray-200 dark:border-gray-800">
          {/* From - Account selector */}
          {accounts && accounts.length > 1 && (
            <div className="flex items-center gap-3">
              <label className="w-12 text-sm text-gray-500 dark:text-gray-400">From</label>
              <div className="relative flex-1">
                <button
                  type="button"
                  onClick={() => setShowFromDropdown(!showFromDropdown)}
                  className="w-full flex items-center gap-3 px-3 py-2 text-left bg-gray-50 dark:bg-gray-800 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700"
                >
                  <div className="w-6 h-6 rounded-full bg-brand-100 dark:bg-brand-900/30 flex items-center justify-center flex-shrink-0">
                    <Mail className="w-3 h-3 text-brand-600" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <span className="text-gray-900 dark:text-white truncate">
                      {fromAccount?.from_name || fromAccount?.email_address?.split('@')[0]}
                    </span>
                    <span className="text-gray-500 dark:text-gray-400 ml-1">
                      &lt;{fromAccount?.email_address}&gt;
                    </span>
                  </div>
                  <ChevronDown className="w-4 h-4 text-gray-400" />
                </button>

                {showFromDropdown && (
                  <div className="absolute top-full left-0 right-0 mt-1 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg z-50 overflow-hidden">
                    {accounts.map((account) => (
                      <button
                        key={account.id}
                        type="button"
                        onClick={() => {
                          setFromAccountId(account.id)
                          setShowFromDropdown(false)
                        }}
                        className={`w-full flex items-center gap-3 px-3 py-2 text-left hover:bg-gray-100 dark:hover:bg-gray-700 ${
                          account.id === fromAccountId ? 'bg-brand-50 dark:bg-brand-900/20' : ''
                        }`}
                      >
                        <div className="w-6 h-6 rounded-full bg-brand-100 dark:bg-brand-900/30 flex items-center justify-center flex-shrink-0">
                          <Mail className="w-3 h-3 text-brand-600" />
                        </div>
                        <div className="flex-1 min-w-0">
                          <span className="text-gray-900 dark:text-white">
                            {account.from_name || account.email_address.split('@')[0]}
                          </span>
                          <span className="text-gray-500 dark:text-gray-400 ml-1 text-sm">
                            &lt;{account.email_address}&gt;
                          </span>
                        </div>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* To */}
          <div className="flex items-start gap-3">
            <label className="w-12 text-sm text-gray-500 dark:text-gray-400 pt-2">To</label>
            <div className="flex-1">
              <ContactInput
                value={toList}
                onChange={setToList}
                placeholder="recipient@example.com"
              />
            </div>
            <div className="flex gap-2 text-sm pt-2">
              {!showCc && (
                <button onClick={() => setShowCc(true)} className="text-gray-500 hover:text-gray-700">
                  Cc
                </button>
              )}
              {!showBcc && (
                <button onClick={() => setShowBcc(true)} className="text-gray-500 hover:text-gray-700">
                  Bcc
                </button>
              )}
            </div>
          </div>

          {/* Cc */}
          {showCc && (
            <div className="flex items-start gap-3">
              <label className="w-12 text-sm text-gray-500 dark:text-gray-400 pt-2">Cc</label>
              <div className="flex-1">
                <ContactInput
                  value={ccList}
                  onChange={setCcList}
                  placeholder="cc@example.com"
                />
              </div>
            </div>
          )}

          {/* Bcc */}
          {showBcc && (
            <div className="flex items-start gap-3">
              <label className="w-12 text-sm text-gray-500 dark:text-gray-400 pt-2">Bcc</label>
              <div className="flex-1">
                <ContactInput
                  value={bccList}
                  onChange={setBccList}
                  placeholder="bcc@example.com"
                />
              </div>
            </div>
          )}

          {/* Subject */}
          <div className="flex items-center gap-3">
            <label className="w-12 text-sm text-gray-500 dark:text-gray-400">Subject</label>
            <input
              type="text"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              placeholder="Subject"
              className="flex-1 px-3 py-2 bg-transparent border-none text-gray-900 dark:text-white focus:ring-0"
            />
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 p-4">
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder="Write your message..."
            className="w-full h-full resize-none bg-transparent border-none text-gray-900 dark:text-white focus:ring-0"
          />
        </div>

        {/* Attachments */}
        {attachments.length > 0 && (
          <div className="px-4 py-2 border-t border-gray-200 dark:border-gray-800">
            <div className="flex flex-wrap gap-2">
              {attachments.map((att, index) => (
                <div
                  key={index}
                  className="flex items-center gap-2 px-3 py-1.5 bg-gray-100 dark:bg-gray-800 rounded-lg text-sm"
                >
                  <Paperclip className="w-3.5 h-3.5 text-gray-400" />
                  <span className="text-gray-700 dark:text-gray-300 truncate max-w-[200px]">
                    {att.filename}
                  </span>
                  <span className="text-gray-400 text-xs">
                    ({formatFileSize(att.size)})
                  </span>
                  <button
                    onClick={() => removeAttachment(index)}
                    className="p-0.5 text-gray-400 hover:text-red-500"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Toolbar */}
        <div className="flex items-center gap-2 p-4 border-t border-gray-200 dark:border-gray-800">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={handleFileSelect}
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            className="p-2 text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800"
            title="Attach files"
          >
            <Paperclip className="w-5 h-5" />
          </button>
        </div>
      </div>

      {/* Error */}
      {sendMutation.error && (
        <div className="p-4 bg-red-50 dark:bg-red-900/20 border-t border-red-200 dark:border-red-800 text-red-700 dark:text-red-400">
          {sendMutation.error.response?.data?.detail || sendMutation.error.message}
        </div>
      )}
    </div>
  )
}
