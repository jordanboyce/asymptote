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

// Shorten long account identifiers (NetX360 HBIL includes full name+address)
const _shortAccount = (raw) => {
  if (!raw) return 'Unknown Account'
  const s = String(raw).replace(/\s+/g, ' ').trim()
  // If it fits on one line, keep it
  if (s.length <= 45) return s
  // Take the first line-break chunk (name before address lines)
  const firstChunk = s.split(/\d{3,5}\s+[A-Z]|\b[A-Z]{2}\s+\d{5}/)[0].trim()
  return firstChunk.length > 10 ? firstChunk.slice(0, 42).trimEnd() + '…' : s.slice(0, 42) + '…'
}

const _fmtMoney = (n) => {
  if (n == null) return '—'
  const abs = Math.abs(n)
  const sign = n < 0 ? '-' : ''
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toLocaleString(undefined, { maximumFractionDigits: 2 })}M`
  return `${sign}$${abs.toLocaleString(undefined, { maximumFractionDigits: 0 })}`
}

const formatBrief = (brief) => {
  if (!brief) return 'No portfolio data found in this collection.'
  const lines = []
  const fmt = (n) => (n == null ? '—' : typeof n === 'number' ? n.toLocaleString(undefined, { maximumFractionDigits: 2 }) : String(n))

  // Household summary
  // NOTE: brief_generator uses keys: total_market_value, total_cost_basis, total_unrealized_pnl
  const hs = brief.household_summary || {}
  lines.push('MEETING BRIEF', '═'.repeat(40))
  lines.push(`Generated: ${brief.generated_at ? new Date(brief.generated_at).toLocaleString() : '—'}`, '')
  lines.push('HOUSEHOLD SUMMARY', '─'.repeat(20))
  if (hs.total_market_value != null) lines.push(`Market Value:    ${_fmtMoney(hs.total_market_value)}`)
  if (hs.total_cost_basis != null)   lines.push(`Cost Basis:      ${_fmtMoney(hs.total_cost_basis)}`)
  const pnl = hs.total_unrealized_pnl ?? hs.total_unrealized_gl ?? null
  if (pnl != null) {
    const sign = pnl >= 0 ? '+' : ''
    lines.push(`Unrealized G/L:  ${sign}${_fmtMoney(pnl)}`)
  }
  lines.push('')

  // Accounts — deduplicate by key and truncate long names from HBIL exports
  if (brief.accounts?.length) {
    lines.push('ACCOUNTS', '─'.repeat(20))
    const seen = new Set()
    for (const acc of brief.accounts) {
      const label = _shortAccount(acc.account || acc.account_type || 'Account')
      const key = `${label}|${acc.market_value}`
      if (seen.has(key)) continue
      seen.add(key)
      lines.push(`${label}: ${_fmtMoney(acc.market_value)}`)
    }
    lines.push('')
  }

  // Top positions — deduplicate by name+value (same position may appear from both files)
  if (brief.top_positions?.length) {
    lines.push('TOP POSITIONS', '─'.repeat(20))
    const seen = new Set()
    // Sort by market_value descending, deduplicate, take top 10
    const sorted = [...brief.top_positions].sort((a, b) => (b.market_value || 0) - (a.market_value || 0))
    const totalMv = hs.total_market_value || 0
    let shown = 0
    for (const p of sorted) {
      // brief_generator returns 'name' (from name_col) and optionally 'ticker'
      const sym = p.ticker || p.name || p.symbol || p.description || '(unnamed)'
      const mv = p.market_value || 0
      const dedupKey = `${sym}|${Math.round(mv)}`
      if (seen.has(dedupKey)) continue
      seen.add(dedupKey)
      const pct = totalMv > 0 ? ` (${(mv / totalMv * 100).toFixed(1)}%)` : ''
      lines.push(`${sym}${pct}: ${_fmtMoney(mv)}`)
      if (++shown >= 10) break
    }
    lines.push('')
  }

  // Tax-loss candidates — brief_generator uses 'name', 'unrealized_loss' (positive number = loss)
  if (brief.tax_loss_candidates?.length) {
    lines.push(`⚠️  TAX-LOSS CANDIDATES (${brief.tax_loss_candidates.length})`, '─'.repeat(20))
    const seen = new Set()
    for (const p of brief.tax_loss_candidates) {
      const sym = p.ticker || p.name || p.symbol || p.description || '(unnamed)'
      const loss = p.unrealized_loss ?? p.unrealized_gl ?? 0
      const dedupKey = `${sym}|${Math.round(loss)}`
      if (seen.has(dedupKey)) continue
      seen.add(dedupKey)
      lines.push(`${sym}: -${_fmtMoney(Math.abs(loss))} unrealized loss`)
    }
    lines.push('')
  }

  // Concentration alerts — brief_generator uses 'name', 'pct_of_portfolio'
  if (brief.concentration_alerts?.length) {
    lines.push(`🔴 CONCENTRATION ALERTS (${brief.concentration_alerts.length})`, '─'.repeat(20))
    const seen = new Set()
    for (const p of brief.concentration_alerts) {
      const sym = p.ticker || p.name || p.symbol || p.description || '(unnamed)'
      const pct = p.pct_of_portfolio ?? p.weight_pct
      if (seen.has(sym)) continue
      seen.add(sym)
      lines.push(`${sym}: ${pct != null ? fmt(pct) + '%' : '—'} of portfolio`)
    }
    lines.push('')
  }

  // Cash drag — brief_generator uses 'name', dedup by value
  if (brief.cash_drag_alerts?.length) {
    lines.push(`💵 CASH DRAG (${brief.cash_drag_alerts.length} position${brief.cash_drag_alerts.length !== 1 ? 's' : ''})`, '─'.repeat(20))
    const seen = new Set()
    for (const p of brief.cash_drag_alerts) {
      const desc = p.ticker || p.name || p.description || p.symbol || 'Cash'
      const mv = p.market_value || 0
      const key = `${Math.round(mv)}`
      if (seen.has(key)) continue
      seen.add(key)
      lines.push(`${desc}: ${_fmtMoney(mv)}`)
    }
    lines.push('')
  }

  // Sector allocation — brief_generator uses 'sector', 'market_value' (no weight_pct — compute it)
  if (brief.sector_allocation?.length) {
    lines.push('SECTOR ALLOCATION', '─'.repeat(20))
    const totalMv = hs.total_market_value || brief.sector_allocation.reduce((s, r) => s + (r.market_value || 0), 0)
    for (const s of brief.sector_allocation) {
      const pct = totalMv > 0 ? ((s.market_value || 0) / totalMv * 100).toFixed(1) : '—'
      // brief_generator uses 'weight_pct' OR compute from market_value
      const displayPct = s.weight_pct != null ? fmt(s.weight_pct) : pct
      lines.push(`${s.sector || '?'}: ${displayPct}%`)
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
