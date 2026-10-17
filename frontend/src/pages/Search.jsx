import { useState, useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Search as SearchIcon, RefreshCw } from 'lucide-react'
import { mailApi } from '../services/api'
import MessageList from '../components/MessageList'

export default function Search() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [query, setQuery] = useState(searchParams.get('q') || '')

  const {
    data,
    isLoading,
    refetch,
  } = useQuery({
    queryKey: ['search', query],
    queryFn: () => mailApi.search({ q: query }).then((r) => r.data),
    enabled: !!query && query.length >= 2,
  })

  useEffect(() => {
    const q = searchParams.get('q')
    if (q) {
      setQuery(q)
    }
  }, [searchParams])

  const handleSearch = (e) => {
    e.preventDefault()
    if (query.trim()) {
      setSearchParams({ q: query })
      refetch()
    }
  }

  const messages = data?.emails?.list || data?.results || []

  return (
    <div className="flex flex-col h-full">
      {/* Search Header */}
      <div className="px-4 py-4 bg-white border-b border-gray-200">
        <form onSubmit={handleSearch} className="max-w-2xl">
          <div className="relative">
            <SearchIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search mail..."
              className="w-full pl-10 pr-4 py-3 bg-gray-100 border border-transparent rounded-lg focus:bg-white focus:border-primary-300 focus:ring-2 focus:ring-primary-100 outline-none transition-all"
            />
          </div>
        </form>
      </div>

      {/* Results */}
      <div className="flex-1 overflow-y-auto bg-white">
        {isLoading ? (
          <div className="flex items-center justify-center h-full">
            <RefreshCw className="w-8 h-8 text-gray-400 animate-spin" />
          </div>
        ) : query.length < 2 ? (
          <div className="flex flex-col items-center justify-center h-full text-gray-500">
            <SearchIcon className="w-16 h-16 text-gray-300 mb-4" />
            <p className="text-lg">Search your mail</p>
            <p className="text-sm">Enter at least 2 characters to search</p>
          </div>
        ) : messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-gray-500">
            <p className="text-lg">No results found</p>
            <p className="text-sm">Try a different search term</p>
          </div>
        ) : (
          <div>
            <div className="px-4 py-2 text-sm text-gray-500 border-b border-gray-200">
              {messages.length} result{messages.length !== 1 && 's'} for "{query}"
            </div>
            <MessageList messages={messages} />
          </div>
        )}
      </div>
    </div>
  )
}
