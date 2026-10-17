/**
 * Parse a Gmail-style search query into structured params.
 *
 * Supports:
 *   from:sender@example.com
 *   to:recipient@example.com
 *   subject:keyword
 *   is:unread, is:read, is:starred, is:flagged
 *   has:attachment
 *   before:2024-01-01
 *   after:2024-01-01
 *   Free text (everything else becomes the query string)
 *
 * Returns: { query, from, to, subject, is_unread, is_flagged, has_attachments, before, after }
 */
export function parseSearchQuery(searchString) {
  if (!searchString) return {}

  const params = {}
  const freeText = []

  // Match operator:value patterns (supporting quoted values)
  const regex = /(\w+):(?:"([^"]+)"|(\S+))/g
  let remaining = searchString
  let match

  while ((match = regex.exec(searchString)) !== null) {
    const operator = match[1].toLowerCase()
    const value = match[2] || match[3]
    remaining = remaining.replace(match[0], '')

    switch (operator) {
      case 'from':
        params.from_address = value
        break
      case 'to':
        params.to_address = value
        break
      case 'subject':
        params.subject = value
        break
      case 'is':
        if (value === 'unread') params.is_unread = true
        else if (value === 'read') params.is_unread = false
        else if (value === 'starred' || value === 'flagged') params.is_flagged = true
        else if (value === 'unstarred') params.is_flagged = false
        break
      case 'has':
        if (value === 'attachment' || value === 'attachments') params.has_attachments = true
        break
      case 'before':
        params.before = value
        break
      case 'after':
        params.after = value
        break
      default:
        // Unknown operator, keep as free text
        freeText.push(match[0])
    }
  }

  // Whatever's left is the free text query
  const query = remaining.trim()
  if (query) {
    params.query = query
  }

  return params
}

/**
 * Format parsed search params back into search chips for display.
 */
export function getSearchChips(searchString) {
  if (!searchString) return []

  const chips = []
  const regex = /(\w+):(?:"([^"]+)"|(\S+))/g
  let match

  while ((match = regex.exec(searchString)) !== null) {
    const operator = match[1].toLowerCase()
    const value = match[2] || match[3]
    chips.push({ operator, value, raw: match[0] })
  }

  return chips
}
