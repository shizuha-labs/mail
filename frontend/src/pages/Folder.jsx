import { useState, useEffect } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { RefreshCw, MoreVertical, CheckSquare, Trash2, Archive } from 'lucide-react'
import { mailApi } from '../services/api'
import { useMail } from '../contexts/MailContext'
import MessageList from '../components/MessageList'
import { cn } from '../utils/cn'

export default function Folder({ folderRole }) {
  const { folderId } = useParams()
  const { folders, getFolderByRole, setSelectedFolder } = useMail()
  const [selectedIds, setSelectedIds] = useState([])
  const [showCheckboxes, setShowCheckboxes] = useState(false)

  // Get folder either by role (sent, drafts, etc.) or by ID
  const folder = folderRole
    ? getFolderByRole(folderRole)
    : folders?.find((f) => f.id === folderId)

  useEffect(() => {
    if (folder) {
      setSelectedFolder(folder)
    }
  }, [folder, setSelectedFolder])

  const {
    data,
    isLoading,
    isFetching,
    refetch,
  } = useQuery({
    queryKey: ['messages', folder?.id],
    queryFn: () =>
      mailApi.getMessages({ folder: folder?.id }).then((r) => r.data),
    enabled: !!folder,
  })

  const messages = data?.results || data || []

  const handleSelect = (id) => {
    setSelectedIds((prev) =>
      prev.includes(id) ? prev.filter((i) => i !== id) : [...prev, id]
    )
  }

  const handleSelectAll = () => {
    if (selectedIds.length === messages.length) {
      setSelectedIds([])
    } else {
      setSelectedIds(messages.map((m) => m.id))
    }
  }

  const folderName = folder?.name || 'Folder'

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-4 py-3 bg-white border-b border-gray-200">
        <h1 className="text-lg font-semibold text-gray-900">{folderName}</h1>
      </div>

      {/* Toolbar */}
      <div className="flex items-center justify-between px-4 py-2 bg-white border-b border-gray-200">
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowCheckboxes(!showCheckboxes)}
            className={cn(
              'p-2 rounded-lg transition-colors',
              showCheckboxes ? 'bg-primary-100 text-primary-600' : 'hover:bg-gray-100 text-gray-600'
            )}
            title="Select messages"
          >
            <CheckSquare className="w-5 h-5" />
          </button>

          {selectedIds.length > 0 && (
            <>
              <button
                onClick={handleSelectAll}
                className="text-sm text-primary-600 hover:underline"
              >
                {selectedIds.length === messages.length ? 'Deselect all' : 'Select all'}
              </button>
              <span className="text-sm text-gray-500">
                {selectedIds.length} selected
              </span>
              <div className="h-4 w-px bg-gray-300 mx-2" />
              <button className="p-2 hover:bg-gray-100 rounded-lg text-gray-600" title="Archive">
                <Archive className="w-5 h-5" />
              </button>
              <button className="p-2 hover:bg-gray-100 rounded-lg text-gray-600" title="Delete">
                <Trash2 className="w-5 h-5" />
              </button>
            </>
          )}
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="p-2 hover:bg-gray-100 rounded-lg text-gray-600 disabled:opacity-50"
            title="Refresh"
          >
            <RefreshCw className={cn('w-5 h-5', isFetching && 'animate-spin')} />
          </button>
          <button className="p-2 hover:bg-gray-100 rounded-lg text-gray-600" title="More options">
            <MoreVertical className="w-5 h-5" />
          </button>
        </div>
      </div>

      {/* Message List */}
      <div className="flex-1 overflow-y-auto bg-white">
        {isLoading ? (
          <div className="flex items-center justify-center h-full">
            <RefreshCw className="w-8 h-8 text-gray-400 animate-spin" />
          </div>
        ) : (
          <MessageList
            messages={messages}
            selectedIds={selectedIds}
            onSelect={handleSelect}
            showCheckboxes={showCheckboxes}
          />
        )}
      </div>

      {/* Status bar */}
      {data && (
        <div className="px-4 py-2 bg-gray-50 border-t border-gray-200 text-xs text-gray-500">
          {messages.length} messages
          {folder?.unread_messages > 0 && (
            <span> · {folder.unread_messages} unread</span>
          )}
        </div>
      )}
    </div>
  )
}
