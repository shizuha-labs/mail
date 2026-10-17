import { X } from 'lucide-react'
import { getShortcutsList } from '../hooks/useKeyboardShortcuts'

export default function KeyboardShortcutsHelp({ onClose }) {
  const shortcuts = getShortcutsList()

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div
        className="bg-white dark:bg-gray-800 rounded-xl shadow-2xl max-w-lg w-full mx-4 max-h-[80vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between p-4 border-b border-gray-200 dark:border-gray-700">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white">Keyboard Shortcuts</h2>
          <button
            onClick={onClose}
            className="p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 rounded"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-4 space-y-6">
          {Object.entries(shortcuts).map(([category, items]) => (
            <div key={category}>
              <h3 className="text-sm font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-2">
                {category}
              </h3>
              <div className="space-y-1">
                {items.map(({ key, description }) => (
                  <div key={key} className="flex items-center justify-between py-1.5">
                    <span className="text-sm text-gray-700 dark:text-gray-300">{description}</span>
                    <kbd className="px-2 py-1 text-xs font-mono bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 rounded border border-gray-200 dark:border-gray-600">
                      {key}
                    </kbd>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>

        <div className="p-4 border-t border-gray-200 dark:border-gray-700 text-center">
          <span className="text-sm text-gray-500 dark:text-gray-400">
            Press <kbd className="px-1.5 py-0.5 text-xs font-mono bg-gray-100 dark:bg-gray-700 rounded border border-gray-200 dark:border-gray-600">?</kbd> to toggle this dialog
          </span>
        </div>
      </div>
    </div>
  )
}
