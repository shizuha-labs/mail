import { useState, useRef, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { contactsApi } from '../services/api'
import { X } from 'lucide-react'

export default function ContactInput({ value, onChange, placeholder, className }) {
  // value is an array of email strings, onChange receives updated array
  const [inputValue, setInputValue] = useState('')
  const [showSuggestions, setShowSuggestions] = useState(false)
  const [selectedSuggestion, setSelectedSuggestion] = useState(0)
  const inputRef = useRef(null)
  const containerRef = useRef(null)

  const { data: suggestions } = useQuery({
    queryKey: ['contacts-autocomplete', inputValue],
    queryFn: () => contactsApi.autocomplete(inputValue).then(r => r.data?.results || r.data || []),
    enabled: inputValue.length >= 2,
    staleTime: 10000,
  })

  useEffect(() => {
    setSelectedSuggestion(0)
  }, [suggestions])

  // Close suggestions on outside click
  useEffect(() => {
    const handleClick = (e) => {
      if (containerRef.current && !containerRef.current.contains(e.target)) {
        setShowSuggestions(false)
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  const addContact = (email) => {
    const trimmed = email.trim()
    if (trimmed && !value.includes(trimmed)) {
      onChange([...value, trimmed])
    }
    setInputValue('')
    setShowSuggestions(false)
    inputRef.current?.focus()
  }

  const removeContact = (index) => {
    onChange(value.filter((_, i) => i !== index))
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' || e.key === 'Tab' || e.key === ',') {
      e.preventDefault()
      if (showSuggestions && suggestions?.length > 0) {
        const s = suggestions[selectedSuggestion]
        addContact(s.email || s.email_address || s)
      } else if (inputValue.trim()) {
        addContact(inputValue)
      }
    } else if (e.key === 'Backspace' && !inputValue && value.length > 0) {
      removeContact(value.length - 1)
    } else if (e.key === 'ArrowDown' && showSuggestions) {
      e.preventDefault()
      setSelectedSuggestion(prev => Math.min(prev + 1, (suggestions?.length || 1) - 1))
    } else if (e.key === 'ArrowUp' && showSuggestions) {
      e.preventDefault()
      setSelectedSuggestion(prev => Math.max(prev - 1, 0))
    } else if (e.key === 'Escape') {
      setShowSuggestions(false)
    }
  }

  const handleInputChange = (e) => {
    setInputValue(e.target.value)
    setShowSuggestions(e.target.value.length >= 2)
  }

  return (
    <div ref={containerRef} className={`relative flex flex-wrap items-center gap-1 ${className || ''}`}>
      {/* Contact chips */}
      {value.map((email, index) => (
        <span
          key={index}
          className="inline-flex items-center gap-1 px-2 py-0.5 bg-brand-100 dark:bg-brand-900/30 text-brand-800 dark:text-brand-300 rounded-full text-sm"
        >
          {email}
          <button
            type="button"
            onClick={() => removeContact(index)}
            className="hover:text-brand-600 dark:hover:text-brand-200"
          >
            <X className="w-3 h-3" />
          </button>
        </span>
      ))}

      {/* Input */}
      <input
        ref={inputRef}
        type="text"
        value={inputValue}
        onChange={handleInputChange}
        onKeyDown={handleKeyDown}
        onFocus={() => inputValue.length >= 2 && setShowSuggestions(true)}
        placeholder={value.length === 0 ? placeholder : ''}
        className="flex-1 min-w-[120px] bg-transparent border-none text-gray-900 dark:text-white focus:ring-0 px-1 py-1 text-sm"
      />

      {/* Suggestions dropdown */}
      {showSuggestions && suggestions?.length > 0 && (
        <div className="absolute top-full left-0 right-0 mt-1 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg z-50 max-h-48 overflow-y-auto">
          {suggestions.map((contact, index) => {
            const email = contact.email || contact.email_address || contact
            const name = contact.name || contact.display_name || ''
            return (
              <button
                key={index}
                type="button"
                onClick={() => addContact(email)}
                className={`w-full px-3 py-2 text-left text-sm hover:bg-gray-100 dark:hover:bg-gray-700 ${
                  index === selectedSuggestion ? 'bg-gray-100 dark:bg-gray-700' : ''
                }`}
              >
                {name ? (
                  <div>
                    <span className="text-gray-900 dark:text-white">{name}</span>
                    <span className="text-gray-500 dark:text-gray-400 ml-2">&lt;{email}&gt;</span>
                  </div>
                ) : (
                  <span className="text-gray-900 dark:text-white">{email}</span>
                )}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
