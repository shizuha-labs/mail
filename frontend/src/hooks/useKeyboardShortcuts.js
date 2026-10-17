import { useEffect, useCallback } from 'react'

const SHORTCUTS = {
  global: {
    'c': { action: 'compose', description: 'Compose new email' },
    '/': { action: 'search', description: 'Focus search bar' },
    '?': { action: 'help', description: 'Show keyboard shortcuts' },
  },
  list: {
    'j': { action: 'next', description: 'Next message' },
    'k': { action: 'prev', description: 'Previous message' },
    'x': { action: 'select', description: 'Select/deselect message' },
    'e': { action: 'archive', description: 'Archive selected' },
    '#': { action: 'trash', description: 'Delete selected' },
    's': { action: 'star', description: 'Toggle star' },
    'Enter': { action: 'open', description: 'Open message' },
    'I': { action: 'markRead', description: 'Mark as read', shift: true },
    'U': { action: 'markUnread', description: 'Mark as unread', shift: true },
  },
  message: {
    'r': { action: 'reply', description: 'Reply' },
    'a': { action: 'replyAll', description: 'Reply All' },
    'f': { action: 'forward', description: 'Forward' },
    'e': { action: 'archive', description: 'Archive' },
    '#': { action: 'trash', description: 'Delete' },
    'Escape': { action: 'back', description: 'Back to list' },
    'p': { action: 'print', description: 'Print email' },
  },
}

export function useKeyboardShortcuts(handlers = {}, context = 'global') {
  const handleKeyDown = useCallback((e) => {
    // Don't trigger shortcuts when typing in inputs
    const tag = e.target.tagName.toLowerCase()
    const isInput = tag === 'input' || tag === 'textarea' || tag === 'select' || e.target.isContentEditable

    // Allow '/' to blur and focus search even from non-inputs
    // Allow Escape from anywhere
    if (isInput && e.key !== 'Escape') return

    // Build the key identifier
    const key = e.shiftKey && e.key.length === 1 ? e.key.toUpperCase() : e.key

    // Check context-specific shortcuts first, then global
    const contextShortcuts = SHORTCUTS[context] || {}
    const shortcut = contextShortcuts[key] || SHORTCUTS.global[key]

    if (shortcut && handlers[shortcut.action]) {
      // For shift shortcuts, verify shift is held
      if (shortcut.shift && !e.shiftKey) return

      e.preventDefault()
      handlers[shortcut.action](e)
    }
  }, [handlers, context])

  useEffect(() => {
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [handleKeyDown])
}

export function getShortcutsList() {
  return {
    'General': Object.entries(SHORTCUTS.global).map(([key, s]) => ({
      key: key === '/' ? '/' : key,
      description: s.description,
    })),
    'Message List': Object.entries(SHORTCUTS.list).map(([key, s]) => ({
      key: s.shift ? `Shift+${key}` : key,
      description: s.description,
    })),
    'Reading Message': Object.entries(SHORTCUTS.message).map(([key, s]) => ({
      key,
      description: s.description,
    })),
  }
}
