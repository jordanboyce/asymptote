import axios from 'axios'

// Shared slash commands used by both Chat and Search. Each command reads
// collection metadata directly and returns a plain-text block — zero tokens,
// instant, deterministic.

export const SLASH_COMMANDS = {
  '/brief': 'Generate meeting brief for this collection',
  '/stats': 'Show collection statistics',
  '/docs': 'List indexed documents',
  '/help': 'Show available slash commands',
}

export const filterCommands = (input) => {
  const trimmed = (input || '').trim().toLowerCase()
  if (!trimmed.startsWith('/')) return Object.entries(SLASH_COMMANDS)
  return Object.entries(SLASH_COMMANDS).filter(([cmd]) =>
    cmd.toLowerCase().startsWith(trimmed),
  )
}

export const isSlashCommand = (input) => {
  const trimmed = (input || '').trim()
  if (!trimmed.startsWith('/')) return false
  const first = trimmed.split(/\s+/)[0].toLowerCase()
  return Object.prototype.hasOwnProperty.call(SLASH_COMMANDS, first)
}

const formatStats = (collection, documents) => {
  const totalDocs = documents.length
  const totalPages = documents.reduce((sum, d) => sum + (d.total_pages || 0), 0)
  const totalChunks = documents.reduce((sum, d) => sum + (d.total_chunks || 0), 0)

  const timestamps = documents
    .map((d) => d.indexed_at)
    .filter(Boolean)
    .sort()
  const dateRange = timestamps.length
    ? `${timestamps[0].slice(0, 10)} → ${timestamps[timestamps.length - 1].slice(0, 10)}`
    : '—'

  const fileTypes = {}
  for (const d of documents) {
    const ext = d.filename.includes('.')
      ? d.filename.split('.').pop().toLowerCase()
      : 'other'
    fileTypes[ext] = (fileTypes[ext] || 0) + 1
  }
  const fileTypesStr = Object.entries(fileTypes)
    .sort((a, b) => b[1] - a[1])
    .map(([ext, count]) => `${ext} (${count})`)
    .join(', ') || '—'

  const title = `${collection?.name || 'Collection'} — Stats`
  const lines = [title, '─'.repeat(Math.min(title.length, 48))]
  if (collection?.description) lines.push(collection.description, '')
  lines.push(
    `Documents:     ${totalDocs}`,
    `Pages:         ${totalPages}`,
    `Chunks:        ${totalChunks}`,
    `File types:    ${fileTypesStr}`,
    `Indexed range: ${dateRange}`,
  )
  return lines.join('\n')
}

const formatDocs = (collection, documents) => {
  const title = `${collection?.name || 'Collection'} — ${documents.length} document${documents.length === 1 ? '' : 's'}`
  if (!documents.length) {
    return `${title}\n${'─'.repeat(Math.min(title.length, 48))}\n\nNo documents indexed yet.`
  }

  const sorted = [...documents].sort((a, b) => a.filename.localeCompare(b.filename))
  const MAX_ROWS = 100
  const visible = sorted.slice(0, MAX_ROWS)

  const idxWidth = String(visible.length).length
  const nameWidth = Math.min(
    Math.max(...visible.map((d) => d.filename.length), 8),
    60,
  )
  const pagesWidth = Math.max(
    ...visible.map((d) => String(d.total_pages || 0).length),
    5,
  )
  const chunksWidth = Math.max(
    ...visible.map((d) => String(d.total_chunks || 0).length),
    6,
  )

  const truncate = (s, n) => (s.length <= n ? s : s.slice(0, n - 1) + '…')
  const pad = (s, n, right = false) =>
    right ? String(s).padStart(n) : String(s).padEnd(n)

  const lines = [title, '─'.repeat(Math.min(title.length, 60)), '']
  lines.push(
    `${pad('#', idxWidth)}  ${pad('Filename', nameWidth)}  ${pad('Pages', pagesWidth, true)}  ${pad('Chunks', chunksWidth, true)}`,
  )
  lines.push(
    `${'─'.repeat(idxWidth)}  ${'─'.repeat(nameWidth)}  ${'─'.repeat(pagesWidth)}  ${'─'.repeat(chunksWidth)}`,
  )
  for (let i = 0; i < visible.length; i++) {
    const d = visible[i]
    lines.push(
      `${pad(i + 1, idxWidth, true)}  ${pad(truncate(d.filename, nameWidth), nameWidth)}  ${pad(d.total_pages || 0, pagesWidth, true)}  ${pad(d.total_chunks || 0, chunksWidth, true)}`,
    )
  }
  if (sorted.length > MAX_ROWS) {
    lines.push('', `…and ${sorted.length - MAX_ROWS} more`)
  }
  return lines.join('\n')
}

const formatHelp = () => {
  const lines = ['Slash commands', '─'.repeat(14), '']
  for (const [cmd, desc] of Object.entries(SLASH_COMMANDS)) {
    lines.push(`${cmd.padEnd(8)}  ${desc}`)
  }
  return lines.join('\n')
}

const formatBrief = (brief) => {
  if (!brief) return 'No portfolio data found in this collection.'
  const lines = []
  const fmt = (n) => (n == null ? '—' : typeof n === 'number' ? n.toLocaleString(undefined, { maximumFractionDigits: 2 }) : String(n))

  // Household summary
  const hs = brief.household_summary || {}
  lines.push('MEETING BRIEF', '═'.repeat(40))
  lines.push(`Generated: ${brief.generated_at ? new Date(brief.generated_at).toLocaleString() : '—'}`, '')
  lines.push('HOUSEHOLD SUMMARY', '─'.repeat(20))
  if (hs.total_market_value != null) lines.push(`Market Value:    $${fmt(hs.total_market_value)}`)
  if (hs.total_cost_basis != null)   lines.push(`Cost Basis:      $${fmt(hs.total_cost_basis)}`)
  if (hs.total_unrealized_gl != null) {
    const sign = hs.total_unrealized_gl >= 0 ? '+' : ''
    lines.push(`Unrealized G/L:  ${sign}$${fmt(hs.total_unrealized_gl)}`)
  }
  if (hs.num_positions != null) lines.push(`Positions:       ${hs.num_positions}`)
  lines.push('')

  // Accounts
  if (brief.accounts?.length) {
    lines.push('ACCOUNTS', '─'.repeat(20))
    for (const acc of brief.accounts) {
      const label = acc.account || acc.account_type || 'Account'
      lines.push(`${label}: $${fmt(acc.market_value)}`)
    }
    lines.push('')
  }

  // Top positions
  if (brief.top_positions?.length) {
    lines.push('TOP POSITIONS', '─'.repeat(20))
    for (const p of brief.top_positions) {
      const sym = p.symbol || p.description || '?'
      const pct = p.weight_pct != null ? ` (${fmt(p.weight_pct)}%)` : ''
      lines.push(`${sym}${pct}: $${fmt(p.market_value)}`)
    }
    lines.push('')
  }

  // Tax-loss candidates
  if (brief.tax_loss_candidates?.length) {
    lines.push(`⚠️  TAX-LOSS CANDIDATES (${brief.tax_loss_candidates.length})`, '─'.repeat(20))
    for (const p of brief.tax_loss_candidates) {
      const sym = p.symbol || p.description || '?'
      lines.push(`${sym}: $(${fmt(Math.abs(p.unrealized_gl || 0))}) loss`)
    }
    lines.push('')
  }

  // Concentration alerts
  if (brief.concentration_alerts?.length) {
    lines.push(`🔴 CONCENTRATION ALERTS (${brief.concentration_alerts.length})`, '─'.repeat(20))
    for (const p of brief.concentration_alerts) {
      const sym = p.symbol || p.description || '?'
      lines.push(`${sym}: ${fmt(p.weight_pct)}% of portfolio`)
    }
    lines.push('')
  }

  // Cash drag
  if (brief.cash_drag_alerts?.length) {
    lines.push(`💵 CASH DRAG (${brief.cash_drag_alerts.length})`, '─'.repeat(20))
    for (const p of brief.cash_drag_alerts) {
      const desc = p.description || p.symbol || 'Cash'
      lines.push(`${desc}: $${fmt(p.market_value)}`)
    }
    lines.push('')
  }

  // Sector allocation
  if (brief.sector_allocation?.length) {
    lines.push('SECTOR ALLOCATION', '─'.repeat(20))
    for (const s of brief.sector_allocation) {
      lines.push(`${s.sector || '?'}: ${fmt(s.weight_pct)}%`)
    }
  }

  return lines.join('\n')
}

// Parses, validates, and executes a slash command. Returns { cmd, content } on
// success or { cmd, content, error: true } on failure. Callers decide how to
// render the result.
export const runSlashCommand = async (input, { collectionId, collection }) => {
  const cmd = input.trim().split(/\s+/)[0].toLowerCase()
  if (!SLASH_COMMANDS[cmd]) {
    return { cmd, content: `Unknown command: ${cmd}`, error: true }
  }

  try {
    if (cmd === '/help') {
      return { cmd, content: formatHelp() }
    }

    if (cmd === '/brief') {
      const response = await axios.post(`/api/collections/${collectionId}/brief`)
      return { cmd, content: formatBrief(response.data) }
    }

    const response = await axios.get(`/documents?collection_id=${collectionId}`)
    const documents = response.data.documents || []

    if (cmd === '/stats') {
      return { cmd, content: formatStats(collection, documents) }
    }
    if (cmd === '/docs') {
      return { cmd, content: formatDocs(collection, documents) }
    }
  } catch (err) {
    const msg = err.response?.data?.detail || err.message || 'Unknown error'
    return { cmd, content: `Error running ${cmd}: ${msg}`, error: true }
  }

  return { cmd, content: `Command ${cmd} is not implemented`, error: true }
}
