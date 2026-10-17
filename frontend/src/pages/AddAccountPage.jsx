import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useMutation } from '@tanstack/react-query'
import { mailApi } from '../services/api'
import { useMail } from '../contexts/MailContext'
import {
  ArrowLeft,
  Mail,
  Server,
  Lock,
  Eye,
  EyeOff,
  Loader2,
  CheckCircle,
  XCircle,
  ChevronDown,
  ChevronUp,
} from 'lucide-react'

export default function AddAccountPage() {
  const navigate = useNavigate()
  const { refetchAccounts } = useMail()

  // Form state
  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [displayName, setDisplayName] = useState('')
  const [fromName, setFromName] = useState('')

  // Server settings
  const [selectedProvider, setSelectedProvider] = useState(null)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [incomingHost, setIncomingHost] = useState('')
  const [incomingPort, setIncomingPort] = useState(993)
  const [incomingSecurity, setIncomingSecurity] = useState('ssl')
  const [incomingType, setIncomingType] = useState('imap')
  const [outgoingHost, setOutgoingHost] = useState('')
  const [outgoingPort, setOutgoingPort] = useState(587)
  const [outgoingSecurity, setOutgoingSecurity] = useState('starttls')

  // Test results
  const [testResults, setTestResults] = useState(null)

  // Fetch providers
  const { data: providers } = useQuery({
    queryKey: ['providers'],
    queryFn: () => mailApi.getProviders().then((r) => r.data),
  })

  // Auto-detect provider from username (if email-style)
  useEffect(() => {
    if (username && username.includes('@') && providers) {
      const domain = username.split('@')[1]?.toLowerCase()
      if (domain) {
        const matched = providers.find((p) =>
          p.domains?.some((d) => domain === d || domain.endsWith('.' + d))
        )
        if (matched && (!selectedProvider || selectedProvider.id !== matched.id)) {
          applyProvider(matched)
        }
      }
    }
  }, [username, providers])

  // Apply provider settings
  const applyProvider = (provider) => {
    setSelectedProvider(provider)
    setIncomingHost(provider.incoming_host || '')
    setIncomingPort(provider.incoming_port || 993)
    setIncomingSecurity(provider.incoming_security || 'ssl')
    setIncomingType(provider.incoming_server_type || 'imap')
    setOutgoingHost(provider.outgoing_host || '')
    setOutgoingPort(provider.outgoing_port || 587)
    setOutgoingSecurity(provider.outgoing_security || 'starttls')
  }

  // Create account mutation
  const createMutation = useMutation({
    mutationFn: (data) => mailApi.createAccount(data),
    onSuccess: () => {
      refetchAccounts()
      navigate('/accounts')
    },
  })

  // Test connection mutation (tests without creating account)
  const testMutation = useMutation({
    mutationFn: async (data) => {
      const response = await mailApi.testConnection(data)
      return response.data
    },
    onSuccess: (data) => {
      setTestResults(data)
    },
    onError: (error) => {
      const msg = error.response?.data?.error || error.response?.data?.detail || error.message
      setTestResults({
        incoming: { success: false, message: msg },
        outgoing: { success: false, message: msg },
      })
    },
  })

  const handleSubmit = (e) => {
    e.preventDefault()

    const data = {
      email_address: email || `${username}@${getEmailDomain(incomingHost)}`,
      incoming_password: password,
      display_name: displayName || username,
      from_name: fromName,
      incoming_server_type: incomingType,
      incoming_host: incomingHost,
      incoming_port: incomingPort,
      incoming_security: incomingSecurity,
      incoming_username: username,
      outgoing_host: outgoingHost,
      outgoing_port: outgoingPort,
      outgoing_security: outgoingSecurity,
    }

    createMutation.mutate(data)
  }

  const handleTest = () => {
    const data = {
      email_address: email || `${username}@${getEmailDomain(incomingHost)}`,
      incoming_password: password,
      display_name: displayName || username,
      from_name: fromName,
      incoming_server_type: incomingType,
      incoming_host: incomingHost,
      incoming_port: incomingPort,
      incoming_security: incomingSecurity,
      incoming_username: username,
      outgoing_host: outgoingHost,
      outgoing_port: outgoingPort,
      outgoing_security: outgoingSecurity,
    }

    testMutation.mutate(data)
  }

  // Derive email domain from incoming host, stripping common mail server prefixes
  const getEmailDomain = (host) => {
    if (!host) return 'example.com'
    return host.replace(/^(mail|imap|smtp|pop|jmap|mx)\./i, '')
  }

  const isFormValid = username && password && incomingHost && outgoingHost

  return (
    <div className="h-full overflow-y-auto bg-gray-50 dark:bg-gray-900">
      <div className="max-w-2xl mx-auto p-6">
        {/* Header */}
        <div className="flex items-center gap-4 mb-6">
          <button
            onClick={() => navigate('/accounts')}
            className="p-2 hover:bg-gray-200 dark:hover:bg-gray-800 rounded-lg"
          >
            <ArrowLeft className="w-5 h-5 text-gray-600 dark:text-gray-400" />
          </button>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Add Email Account</h1>
        </div>

        <form onSubmit={handleSubmit} className="space-y-6">
          {/* Email Provider Selection */}
          <div className="bg-white dark:bg-gray-800 rounded-lg p-6 shadow-sm">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">
              Email Provider
            </h2>

            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 mb-4">
              {providers?.filter(p => p.id !== 'custom').map((provider) => (
                <button
                  key={provider.id}
                  type="button"
                  onClick={() => applyProvider(provider)}
                  className={`p-3 border rounded-lg text-left transition-colors ${
                    selectedProvider?.id === provider.id
                      ? 'border-brand-500 bg-brand-50 dark:bg-brand-900/20'
                      : 'border-gray-200 dark:border-gray-700 hover:border-gray-300'
                  }`}
                >
                  <div className="font-medium text-gray-900 dark:text-white">
                    {provider.name}
                  </div>
                  <div className="text-xs text-gray-500 dark:text-gray-400">
                    {provider.domains?.[0]}
                  </div>
                </button>
              ))}
              <button
                type="button"
                onClick={() => {
                  setSelectedProvider({ id: 'custom', name: 'Custom' })
                  setShowAdvanced(true)
                }}
                className={`p-3 border rounded-lg text-left transition-colors ${
                  selectedProvider?.id === 'custom'
                    ? 'border-brand-500 bg-brand-50 dark:bg-brand-900/20'
                    : 'border-gray-200 dark:border-gray-700 hover:border-gray-300'
                }`}
              >
                <div className="font-medium text-gray-900 dark:text-white">Custom</div>
                <div className="text-xs text-gray-500 dark:text-gray-400">
                  Manual setup
                </div>
              </button>
            </div>
          </div>

          {/* Basic Settings */}
          <div className="bg-white dark:bg-gray-800 rounded-lg p-6 shadow-sm">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">
              Account Details
            </h2>

            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Username
                </label>
                <div className="relative">
                  <Mail className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
                  <input
                    type="text"
                    value={username}
                    onChange={(e) => {
                      setUsername(e.target.value)
                      // Auto-detect provider from email-style usernames
                      if (e.target.value.includes('@') && providers) {
                        const domain = e.target.value.split('@')[1]?.toLowerCase()
                        if (domain) {
                          const matched = providers.find((p) =>
                            p.domains?.some((d) => domain === d || domain.endsWith('.' + d))
                          )
                          if (matched && (!selectedProvider || selectedProvider.id !== matched.id)) {
                            applyProvider(matched)
                          }
                        }
                        // Auto-fill email if it looks like an email
                        if (!email) setEmail(e.target.value)
                      }
                    }}
                    placeholder="username or user@example.com"
                    className="w-full pl-10 pr-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                    required
                  />
                </div>
                <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                  Your login username for the mail server (may differ from email address)
                </p>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Email Address (optional)
                </label>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder={username.includes('@') ? username : `${username}@${getEmailDomain(incomingHost)}`}
                  className="w-full px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                />
                <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                  Your display email. Auto-filled if left blank.
                </p>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Password
                </label>
                <div className="relative">
                  <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
                  <input
                    type={showPassword ? 'text' : 'password'}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="Your email password or app password"
                    className="w-full pl-10 pr-12 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                    required
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword(!showPassword)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                  >
                    {showPassword ? <EyeOff className="w-5 h-5" /> : <Eye className="w-5 h-5" />}
                  </button>
                </div>
                {selectedProvider?.notes && (
                  <p className="mt-1 text-sm text-amber-600 dark:text-amber-400">
                    {selectedProvider.notes}
                  </p>
                )}
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Display Name (optional)
                </label>
                <input
                  type="text"
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  placeholder="Work, Personal, etc."
                  className="w-full px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Your Name (optional)
                </label>
                <input
                  type="text"
                  value={fromName}
                  onChange={(e) => setFromName(e.target.value)}
                  placeholder="Name shown in From header"
                  className="w-full px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                />
              </div>

              {/* Protocol selector */}
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Incoming Protocol
                </label>
                <div className="flex gap-3">
                  {['imap', 'jmap'].map((proto) => (
                    <button
                      key={proto}
                      type="button"
                      onClick={() => {
                        setIncomingType(proto)
                        if (proto === 'jmap' && incomingPort === 993) {
                          setIncomingPort(8543)
                        } else if (proto === 'imap' && incomingPort === 8543) {
                          setIncomingPort(993)
                        }
                      }}
                      className={`flex-1 py-2 px-4 rounded-lg border text-sm font-medium transition-colors ${
                        incomingType === proto
                          ? 'border-brand-500 bg-brand-50 dark:bg-brand-900/20 text-brand-700 dark:text-brand-400'
                          : 'border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:border-gray-400'
                      }`}
                    >
                      {proto.toUpperCase()}
                      <span className="block text-xs font-normal mt-0.5">
                        {proto === 'imap' ? 'Traditional protocol' : 'Modern, faster sync'}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </div>

          {/* Advanced Settings */}
          <div className="bg-white dark:bg-gray-800 rounded-lg p-6 shadow-sm">
            <button
              type="button"
              onClick={() => setShowAdvanced(!showAdvanced)}
              className="flex items-center justify-between w-full text-lg font-semibold text-gray-900 dark:text-white"
            >
              <span className="flex items-center gap-2">
                <Server className="w-5 h-5" />
                Server Settings
              </span>
              {showAdvanced ? (
                <ChevronUp className="w-5 h-5" />
              ) : (
                <ChevronDown className="w-5 h-5" />
              )}
            </button>

            {showAdvanced && (
              <div className="mt-4 space-y-6">
                {/* Incoming Server */}
                <div>
                  <h3 className="font-medium text-gray-900 dark:text-white mb-3">
                    Incoming Mail (IMAP/JMAP)
                  </h3>
                  <div className="grid grid-cols-2 gap-4">
                    <div className="col-span-2 sm:col-span-1">
                      <label className="block text-sm text-gray-600 dark:text-gray-400 mb-1">
                        Server
                      </label>
                      <input
                        type="text"
                        value={incomingHost}
                        onChange={(e) => setIncomingHost(e.target.value)}
                        placeholder="imap.example.com"
                        className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                      />
                    </div>
                    <div>
                      <label className="block text-sm text-gray-600 dark:text-gray-400 mb-1">
                        Port
                      </label>
                      <input
                        type="number"
                        value={incomingPort}
                        onChange={(e) => setIncomingPort(parseInt(e.target.value))}
                        className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                      />
                    </div>
                    <div>
                      <label className="block text-sm text-gray-600 dark:text-gray-400 mb-1">
                        Security
                      </label>
                      <select
                        value={incomingSecurity}
                        onChange={(e) => setIncomingSecurity(e.target.value)}
                        className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                      >
                        <option value="ssl">SSL/TLS</option>
                        <option value="starttls">STARTTLS</option>
                        <option value="none">None</option>
                      </select>
                    </div>
                    <div>
                      <label className="block text-sm text-gray-600 dark:text-gray-400 mb-1">
                        Protocol
                      </label>
                      <div className="w-full px-3 py-2 border border-gray-200 dark:border-gray-700 rounded-lg bg-gray-50 dark:bg-gray-800 text-gray-600 dark:text-gray-400 text-sm">
                        {incomingType.toUpperCase()} (change above)
                      </div>
                    </div>
                  </div>
                </div>

                {/* Outgoing Server */}
                <div>
                  <h3 className="font-medium text-gray-900 dark:text-white mb-3">
                    Outgoing Mail (SMTP)
                  </h3>
                  <div className="grid grid-cols-2 gap-4">
                    <div className="col-span-2 sm:col-span-1">
                      <label className="block text-sm text-gray-600 dark:text-gray-400 mb-1">
                        Server
                      </label>
                      <input
                        type="text"
                        value={outgoingHost}
                        onChange={(e) => setOutgoingHost(e.target.value)}
                        placeholder="smtp.example.com"
                        className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                      />
                    </div>
                    <div>
                      <label className="block text-sm text-gray-600 dark:text-gray-400 mb-1">
                        Port
                      </label>
                      <input
                        type="number"
                        value={outgoingPort}
                        onChange={(e) => setOutgoingPort(parseInt(e.target.value))}
                        className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                      />
                    </div>
                    <div>
                      <label className="block text-sm text-gray-600 dark:text-gray-400 mb-1">
                        Security
                      </label>
                      <select
                        value={outgoingSecurity}
                        onChange={(e) => setOutgoingSecurity(e.target.value)}
                        className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                      >
                        <option value="ssl">SSL/TLS</option>
                        <option value="starttls">STARTTLS</option>
                        <option value="none">None</option>
                      </select>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Test Results */}
          {testResults && (
            <div className="bg-white dark:bg-gray-800 rounded-lg p-6 shadow-sm">
              <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">
                Connection Test Results
              </h2>
              <div className="space-y-3">
                <div className="flex items-center gap-3">
                  {testResults.incoming?.success ? (
                    <CheckCircle className="w-5 h-5 text-green-500" />
                  ) : (
                    <XCircle className="w-5 h-5 text-red-500" />
                  )}
                  <span className="text-gray-700 dark:text-gray-300">
                    Incoming: {testResults.incoming?.message || (testResults.incoming?.success ? 'Connected' : 'Failed')}
                  </span>
                </div>
                <div className="flex items-center gap-3">
                  {testResults.outgoing?.success ? (
                    <CheckCircle className="w-5 h-5 text-green-500" />
                  ) : (
                    <XCircle className="w-5 h-5 text-red-500" />
                  )}
                  <span className="text-gray-700 dark:text-gray-300">
                    Outgoing: {testResults.outgoing?.message || (testResults.outgoing?.success ? 'Connected' : 'Failed')}
                  </span>
                </div>
              </div>
            </div>
          )}

          {/* Actions */}
          <div className="flex items-center justify-end gap-3">
            <button
              type="button"
              onClick={() => navigate('/accounts')}
              className="px-4 py-2 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleTest}
              disabled={!isFormValid || testMutation.isPending}
              className="px-4 py-2 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-50"
            >
              {testMutation.isPending ? (
                <span className="flex items-center gap-2">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Testing...
                </span>
              ) : (
                'Test Connection'
              )}
            </button>
            <button
              type="submit"
              disabled={!isFormValid || createMutation.isPending}
              className="px-4 py-2 bg-brand-600 text-white rounded-lg hover:bg-brand-700 disabled:opacity-50"
            >
              {createMutation.isPending ? (
                <span className="flex items-center gap-2">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Adding...
                </span>
              ) : (
                'Add Account'
              )}
            </button>
          </div>

          {/* Error */}
          {createMutation.error && (
            <div className="p-4 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg text-red-700 dark:text-red-400">
              {createMutation.error.response?.data?.detail || createMutation.error.message}
            </div>
          )}
        </form>
      </div>
    </div>
  )
}
