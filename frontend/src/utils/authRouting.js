export function normalizeBasePath(basePath = '/') {
  const trimmed = String(basePath || '/').trim()
  const withLeadingSlash = trimmed.startsWith('/') ? trimmed : `/${trimmed}`
  return withLeadingSlash.endsWith('/') ? withLeadingSlash : `${withLeadingSlash}/`
}

export function resolveLoginUrl({ basePath = '/', override = '' } = {}) {
  const configured = String(override || '').trim()
  if (configured) return configured

  const normalizedBase = normalizeBasePath(basePath)
  return normalizedBase === '/' ? '/login' : `${normalizedBase}login`
}

function comparablePath(pathname) {
  if (!pathname || pathname === '/') return pathname || '/'
  return pathname.endsWith('/') ? pathname.slice(0, -1) : pathname
}

export function isExactLoginPath(pathname, loginUrl, origin = 'http://localhost') {
  let loginPath
  try {
    loginPath = new URL(loginUrl, origin).pathname
  } catch {
    return false
  }
  return comparablePath(pathname) === comparablePath(loginPath)
}
