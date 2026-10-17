import { useState, useEffect, useCallback } from 'react'

const STORAGE_KEY = 'shizuha-mail-theme'

function getSystemTheme() {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function getInitialTheme() {
  const stored = localStorage.getItem(STORAGE_KEY)
  if (stored === 'dark' || stored === 'light') return stored
  return getSystemTheme()
}

function applyTheme(theme) {
  if (theme === 'dark') {
    document.documentElement.classList.add('dark')
  } else {
    document.documentElement.classList.remove('dark')
  }
}

export default function useTheme() {
  const [theme, setThemeState] = useState(getInitialTheme)

  useEffect(() => {
    applyTheme(theme)
  }, [theme])

  // Listen for system preference changes when no stored preference
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const handler = (e) => {
      if (!localStorage.getItem(STORAGE_KEY)) {
        setThemeState(e.matches ? 'dark' : 'light')
      }
    }
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  }, [])

  const setTheme = useCallback((newTheme) => {
    if (newTheme === 'system') {
      localStorage.removeItem(STORAGE_KEY)
      setThemeState(getSystemTheme())
    } else {
      localStorage.setItem(STORAGE_KEY, newTheme)
      setThemeState(newTheme)
    }
  }, [])

  const toggleTheme = useCallback(() => {
    const next = theme === 'dark' ? 'light' : 'dark'
    localStorage.setItem(STORAGE_KEY, next)
    setThemeState(next)
  }, [theme])

  const isSystem = !localStorage.getItem(STORAGE_KEY)

  return { theme, setTheme, toggleTheme, isDark: theme === 'dark', isSystem }
}
