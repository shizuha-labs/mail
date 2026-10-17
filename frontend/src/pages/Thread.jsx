import { useParams, useNavigate } from 'react-router-dom'
import { useQueries, useQuery } from '@tanstack/react-query'
import { format } from 'date-fns'
import {
  ArrowLeft,
  Star,
  Archive,
  Trash2,
  Reply,
  Forward,
  MoreVertical,
  Paperclip,
  Download,
} from 'lucide-react'
import { mailApi } from '../services/api'
import { useMail } from '../contexts/MailContext'
import { cn } from '../utils/cn'

export default function Thread() {
  const { threadId } = useParams()
  const navigate = useNavigate()
  const { replyTo, forward } = useMail()

  const { data: thread, isLoading } = useQuery({
    queryKey: ['thread', threadId],
    queryFn: () => mailApi.getThread(threadId).then((r) => r.data),
  })

  // Get message bodies for each message in thread
  const messageQueries = useQueries({
    queries: (thread?.messages || []).map((msg) => ({
      queryKey: ['messageBody', msg.id],
      queryFn: () => mailApi.getMessageBody(msg.id).then((r) => r.data),
      enabled: !!msg.id,
    })),
  })

  const handleBack = () => {
    navigate(-1)
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
      </div>
    )
  }

  if (!thread) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-gray-500">
        <p>Thread not found</p>
        <button
          onClick={handleBack}
          className="mt-4 text-primary-600 hover:underline"
        >
          Go back
        </button>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center gap-4 px-4 py-3 bg-white border-b border-gray-200">
        <button
          onClick={handleBack}
          className="p-2 hover:bg-gray-100 rounded-lg text-gray-600"
        >
          <ArrowLeft className="w-5 h-5" />
        </button>

        <h1 className="flex-1 text-lg font-semibold text-gray-900 truncate">
          {thread.subject || '(no subject)'}
        </h1>

        <div className="flex items-center gap-1">
          <button className="p-2 hover:bg-gray-100 rounded-lg text-gray-600" title="Star">
            <Star className={cn('w-5 h-5', thread.is_starred && 'fill-yellow-400 text-yellow-400')} />
          </button>
          <button className="p-2 hover:bg-gray-100 rounded-lg text-gray-600" title="Archive">
            <Archive className="w-5 h-5" />
          </button>
          <button className="p-2 hover:bg-gray-100 rounded-lg text-gray-600" title="Delete">
            <Trash2 className="w-5 h-5" />
          </button>
          <button className="p-2 hover:bg-gray-100 rounded-lg text-gray-600" title="More">
            <MoreVertical className="w-5 h-5" />
          </button>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto bg-gray-50 p-4">
        <div className="max-w-4xl mx-auto space-y-4">
          {thread.messages?.map((message, index) => {
            const bodyQuery = messageQueries[index]
            const body = bodyQuery?.data

            return (
              <div key={message.id} className="bg-white rounded-lg shadow-sm border border-gray-200">
                {/* Message Header */}
                <div className="flex items-start gap-3 p-4 border-b border-gray-100">
                  <div className="w-10 h-10 rounded-full bg-primary-100 flex items-center justify-center text-primary-600 font-medium flex-shrink-0">
                    {(message.from_address?.name || message.from_address?.email || 'U')[0].toUpperCase()}
                  </div>

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-gray-900">
                        {message.from_address?.name || message.from_address?.email}
                      </span>
                      <span className="text-sm text-gray-500">
                        &lt;{message.from_address?.email}&gt;
                      </span>
                    </div>
                    <div className="text-sm text-gray-500">
                      to {message.to_addresses?.map((r) => r.email).join(', ')}
                    </div>
                  </div>

                  <div className="text-sm text-gray-500 flex-shrink-0">
                    {format(new Date(message.date_received), 'MMM d, yyyy h:mm a')}
                  </div>
                </div>

                {/* Message Body */}
                <div className="p-4">
                  {bodyQuery?.isLoading ? (
                    <div className="animate-pulse">
                      <div className="h-4 bg-gray-200 rounded w-3/4 mb-2"></div>
                      <div className="h-4 bg-gray-200 rounded w-1/2 mb-2"></div>
                      <div className="h-4 bg-gray-200 rounded w-2/3"></div>
                    </div>
                  ) : body?.html_body ? (
                    <div
                      className="message-content prose prose-sm max-w-none"
                      dangerouslySetInnerHTML={{ __html: body.html_body }}
                    />
                  ) : (
                    <pre className="whitespace-pre-wrap font-sans text-sm text-gray-700">
                      {body?.text_body || message.preview}
                    </pre>
                  )}
                </div>

                {/* Attachments */}
                {message.attachments?.length > 0 && (
                  <div className="px-4 pb-4">
                    <div className="border border-gray-200 rounded-lg divide-y divide-gray-200">
                      {message.attachments.map((attachment) => (
                        <div
                          key={attachment.id}
                          className="flex items-center gap-3 p-3 hover:bg-gray-50"
                        >
                          <Paperclip className="w-5 h-5 text-gray-400" />
                          <div className="flex-1 min-w-0">
                            <p className="text-sm font-medium text-gray-900 truncate">
                              {attachment.filename}
                            </p>
                            <p className="text-xs text-gray-500">
                              {(attachment.size_bytes / 1024).toFixed(1)} KB
                            </p>
                          </div>
                          <button className="p-2 hover:bg-gray-200 rounded-lg text-gray-600">
                            <Download className="w-4 h-4" />
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Actions */}
                <div className="flex items-center gap-2 px-4 py-3 border-t border-gray-100 bg-gray-50">
                  <button
                    onClick={() => replyTo(message)}
                    className="flex items-center gap-2 px-4 py-2 text-sm text-gray-700 hover:bg-gray-200 rounded-lg"
                  >
                    <Reply className="w-4 h-4" />
                    Reply
                  </button>
                  <button
                    onClick={() => forward(message, body?.text_body || message.preview)}
                    className="flex items-center gap-2 px-4 py-2 text-sm text-gray-700 hover:bg-gray-200 rounded-lg"
                  >
                    <Forward className="w-4 h-4" />
                    Forward
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
