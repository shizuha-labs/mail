export const connectionStatusPresentation = Object.freeze({
  connected: { icon: 'success', color: 'text-green-500', label: 'Connected' },
  unknown: { icon: 'warning', color: 'text-gray-400', label: 'Not tested' },
  auth_failed: { icon: 'error', color: 'text-red-500', label: 'Auth failed' },
  needs_reauth: {
    icon: 'warning',
    color: 'text-amber-500',
    label: 'Re-authorization required',
  },
  connection_failed: { icon: 'error', color: 'text-red-500', label: 'Connection failed' },
  ssl_error: { icon: 'error', color: 'text-amber-500', label: 'SSL error' },
  needs_attention: {
    icon: 'warning',
    color: 'text-amber-500',
    label: 'Needs attention',
  },
})

export function getConnectionStatusPresentation(status) {
  return connectionStatusPresentation[status] || connectionStatusPresentation.unknown
}
