import { useState, useEffect } from 'react'
import { X, Minus, Maximize2, Minimize2, Paperclip, Send, Trash2 } from 'lucide-react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { mailApi } from '../services/api'
import { useMail } from '../contexts/MailContext'
import { cn } from '../utils/cn'
import toast from 'react-hot-toast'

export default function ComposeModal({ onClose, initialData }) {
  const { selectedAccount } = useMail()
  const queryClient = useQueryClient()
  const [minimized, setMinimized] = useState(false)
  const [maximized, setMaximized] = useState(false)
  const [to, setTo] = useState('')
  const [cc, setCc] = useState('')
  const [bcc, setBcc] = useState('')
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const [showCc, setShowCc] = useState(false)
  const [showBcc, setShowBcc] = useState(false)
  const [isSending, setIsSending] = useState(false)

  useEffect(() => {
    if (initialData) {
      if (initialData.to) {
        setTo(initialData.to.map((r) => r.email || r).join(', '))
      }
      if (initialData.cc) {
        setCc(initialData.cc.map((r) => r.email || r).join(', '))
        setShowCc(true)
      }
      if (initialData.subject) {
        setSubject(initialData.subject)
      }
      if (initialData.body) {
        setBody(initialData.body)
      }
    }
  }, [initialData])

  const sendMutation = useMutation({
    mutationFn: (data) => mailApi.sendEmail(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['messages'] })
      queryClient.invalidateQueries({ queryKey: ['folders'] })
      onClose()
    },
  })

  const parseRecipients = (value) => {
    return value
      .split(',')
      .map((email) => email.trim())
      .filter((email) => email)
      .map((email) => ({ email, name: '' }))
  }

  const handleSend = async () => {
    if (!to.trim()) {
      toast.error('Please enter a recipient')
      return
    }

    setIsSending(true)
    try {
      await sendMutation.mutateAsync({
        to: parseRecipients(to),
        cc: showCc ? parseRecipients(cc) : [],
        bcc: showBcc ? parseRecipients(bcc) : [],
        subject,
        text_body: body,
        in_reply_to: initialData?.inReplyTo,
      })
    } catch (error) {
      console.error('Failed to send email:', error)
      toast.error('Failed to send email. Please try again.')
    } finally {
      setIsSending(false)
    }
  }

  const handleDiscard = () => {
    if (to || subject || body) {
      if (confirm('Discard this message?')) {
        onClose()
      }
    } else {
      onClose()
    }
  }

  if (minimized) {
    return (
      <div className="fixed bottom-0 right-4 w-72 bg-white rounded-t-lg shadow-2xl border border-gray-200 z-50">
        <div
          className="flex items-center justify-between px-4 py-2 bg-gray-800 text-white rounded-t-lg cursor-pointer"
          onClick={() => setMinimized(false)}
        >
          <span className="text-sm font-medium truncate">
            {subject || 'New Message'}
          </span>
          <div className="flex items-center gap-1">
            <button
              onClick={(e) => {
                e.stopPropagation()
                setMinimized(false)
              }}
              className="p-1 hover:bg-gray-700 rounded"
            >
              <Maximize2 className="w-4 h-4" />
            </button>
            <button
              onClick={(e) => {
                e.stopPropagation()
                onClose()
              }}
              className="p-1 hover:bg-gray-700 rounded"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div
      className={cn(
        'fixed bg-white rounded-lg shadow-2xl border border-gray-200 z-50 flex flex-col',
        maximized
          ? 'inset-4'
          : 'bottom-0 right-4 w-[600px] h-[500px]'
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 bg-gray-800 text-white rounded-t-lg">
        <span className="text-sm font-medium">New Message</span>
        <div className="flex items-center gap-1">
          <button
            onClick={() => setMinimized(true)}
            className="p-1 hover:bg-gray-700 rounded"
          >
            <Minus className="w-4 h-4" />
          </button>
          <button
            onClick={() => setMaximized(!maximized)}
            className="p-1 hover:bg-gray-700 rounded"
          >
            {maximized ? (
              <Minimize2 className="w-4 h-4" />
            ) : (
              <Maximize2 className="w-4 h-4" />
            )}
          </button>
          <button
            onClick={handleDiscard}
            className="p-1 hover:bg-gray-700 rounded"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Recipients */}
      <div className="border-b border-gray-200">
        <div className="flex items-center px-4 py-2 border-b border-gray-100">
          <span className="text-sm text-gray-500 w-12">To</span>
          <input
            type="text"
            value={to}
            onChange={(e) => setTo(e.target.value)}
            placeholder="Recipients"
            className="flex-1 outline-none text-sm"
          />
          {!showCc && (
            <button
              onClick={() => setShowCc(true)}
              className="text-sm text-gray-500 hover:text-gray-700"
            >
              Cc
            </button>
          )}
          {!showBcc && (
            <button
              onClick={() => setShowBcc(true)}
              className="text-sm text-gray-500 hover:text-gray-700 ml-2"
            >
              Bcc
            </button>
          )}
        </div>

        {showCc && (
          <div className="flex items-center px-4 py-2 border-b border-gray-100">
            <span className="text-sm text-gray-500 w-12">Cc</span>
            <input
              type="text"
              value={cc}
              onChange={(e) => setCc(e.target.value)}
              placeholder="Carbon copy"
              className="flex-1 outline-none text-sm"
            />
          </div>
        )}

        {showBcc && (
          <div className="flex items-center px-4 py-2 border-b border-gray-100">
            <span className="text-sm text-gray-500 w-12">Bcc</span>
            <input
              type="text"
              value={bcc}
              onChange={(e) => setBcc(e.target.value)}
              placeholder="Blind carbon copy"
              className="flex-1 outline-none text-sm"
            />
          </div>
        )}

        <div className="flex items-center px-4 py-2">
          <span className="text-sm text-gray-500 w-12">Subject</span>
          <input
            type="text"
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            placeholder="Subject"
            className="flex-1 outline-none text-sm"
          />
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-hidden">
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder="Write your message..."
          className="w-full h-full p-4 outline-none resize-none text-sm"
        />
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between px-4 py-3 border-t border-gray-200 bg-gray-50 rounded-b-lg">
        <div className="flex items-center gap-2">
          <button
            onClick={handleSend}
            disabled={isSending}
            className="flex items-center gap-2 bg-primary-600 text-white px-4 py-2 rounded-lg hover:bg-primary-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Send className="w-4 h-4" />
            <span>{isSending ? 'Sending...' : 'Send'}</span>
          </button>
          <button className="p-2 hover:bg-gray-200 rounded-lg text-gray-600">
            <Paperclip className="w-5 h-5" />
          </button>
        </div>
        <button
          onClick={handleDiscard}
          className="p-2 hover:bg-gray-200 rounded-lg text-gray-600"
        >
          <Trash2 className="w-5 h-5" />
        </button>
      </div>
    </div>
  )
}
