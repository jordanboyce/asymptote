import axios from 'axios'

// Shared slash commands used by both Chat and Search. Each command reads
// collection metadata directly and returns a plain-text block — zero tokens,
// instant, deterministic.

export const SLASH_COMMANDS = {
  '/brief': 'Generate meeting brief for this collection (or household if a group is selected)',
  '/notes': 'Draft compliance Note of Record from the last meeting transcript',
  '/followup': 'Draft a client-safe follow-up email from the last meeting',
  '/stats': 'Show collection statistics',
  '/docs': 'List indexed documents',
  '/tools': 'Show what the assistant can do',
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

// Curated, human-readable capability list. Grouped so users can see what the
// assistant can do without reading raw tool schemas. Keep descriptions in
// user-language, not tool-name-language. When tools change in
// services/agent_tools.py, update this list.
const TOOL_CATEGORIES = [
  {
    title: 'DOCUMENTS',
    items: [
      'Search PDFs, text, and code (keyword, semantic, or hybrid)',
      'Pull the full text of a document, page, or section',
      'Browse what\'s indexed across all your collections',
    ],
  },
  {
    title: 'PORTFOLIO TABLES (CSV / Excel)',
    items: [
      'Run read-only SQL or group-by aggregations on imported spreadsheets',
      'Compute metrics: market value, cost basis, P&L, concentration, top/bottom holdings',
      'Break down by sector, asset class, region, or currency',
      'Surface tax-loss candidates and weighted returns',
      'Inspect schema, column types, and sample rows before querying',
    ],
  },
  {
    title: 'MARKET DATA (Yahoo Finance)',
    items: [
      'Historical OHLCV price history for any ticker',
      'Sector, market cap, and asset-class classification',
      'Company profile: CEO, business summary, HQ, employees',
      'Recent news headlines',
    ],
  },
]

const TOOL_EXAMPLES = [
  '"List my top 20 holdings by market value"',
  '"Show my sector breakdown and flag concentration risks"',
  '"What\'s been happening with AAPL in the news this month?"',
  '"Find the passage in the 10-K that mentions supply chain risk"',
  '"What\'s the 1-year return on SPY vs. QQQ?"',
]

const formatTools = () => {
  const title = 'What this assistant can do'
  const lines = [title, '─'.repeat(title.length), '']
  for (const { title: cat, items } of TOOL_CATEGORIES) {
    lines.push(cat)
    for (const item of items) lines.push(`  • ${item}`)
    lines.push('')
  }
  lines.push('Try asking:')
  for (const ex of TOOL_EXAMPLES) lines.push(`  • ${ex}`)
  lines.push(
    '',
    'You can also just describe what you want in plain language — the assistant picks the right tools automatically.',
  )
  return lines.join('\n')
}

// Shorten long account identifiers (NetX360 HBIL includes full name+address)
// Strategy: stop at the first trust/account-role keyword, then at a street
// number, and finally hard-truncate — whichever gives the shortest clean label.
const _shortAccount = (raw) => {
  if (!raw) return 'Unknown Account'
  const s = String(raw).replace(/\s+/g, ' ').trim()
  if (s.length <= 45) return s

  // 1. Split at common trust / account-role keywords (inclusive: keep keyword)
  const roleMatch = s.match(/^(.*?\b(?:TTEE|TRUST|IRA|ROTH|UGMA|UTMA|LLC|INC|CORP|JTWROS|TOD|FBO|DBA)\b)/i)
  if (roleMatch) {
    const candidate = roleMatch[1].trim()
    if (candidate.length >= 6 && candidate.length <= 60) {
      return candidate.length <= 45 ? candidate : candidate.slice(0, 42).trimEnd() + '…'
    }
  }

  // 2. Detect where a US street address starts (digits followed by a direction or street)
  const addrIdx = s.search(/\b\d{2,5}\s+[A-Z]/)
  if (addrIdx > 6) {
    const beforeAddr = s.slice(0, addrIdx).trim()
    return beforeAddr.length <= 45 ? beforeAddr : beforeAddr.slice(0, 42).trimEnd() + '…'
  }

  // 3. Hard truncate
  return s.slice(0, 42).trimEnd() + '…'
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
  // Also filter out phantom header-value rows (e.g. name="Symbol") from multi-account CSVs
  // that haven't been re-indexed yet after the header-row fix.
  const _knownHeaders = new Set(['symbol', 'description', 'account name', 'account number',
    'name', 'ticker', 'quantity', 'price', 'value', 'market value', 'cost basis'])
  if (brief.top_positions?.length) {
    lines.push('TOP POSITIONS', '─'.repeat(20))
    const seen = new Set()
    const sorted = [...brief.top_positions].sort((a, b) => (b.market_value || 0) - (a.market_value || 0))
    const totalMv = hs.total_market_value || 0
    let shown = 0
    for (const p of sorted) {
      const sym = p.ticker || p.name || p.symbol || p.description || '(unnamed)'
      const mv = p.market_value || 0
      // Skip phantom header rows
      if (_knownHeaders.has(sym.toLowerCase().trim())) continue
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
    const seen = new Set()
    const realLosses = []
    for (const p of brief.tax_loss_candidates) {
      const sym = p.ticker || p.name || p.symbol || p.description || '(unnamed)'
      if (_knownHeaders.has(sym.toLowerCase().trim())) continue
      const loss = p.unrealized_loss ?? p.unrealized_gl ?? 0
      const dedupKey = `${sym}|${Math.round(loss)}`
      if (seen.has(dedupKey)) continue
      seen.add(dedupKey)
      realLosses.push({ sym, loss })
    }
    if (realLosses.length) {
      lines.push(`⚠️  TAX-LOSS CANDIDATES (${realLosses.length})`, '─'.repeat(20))
      for (const { sym, loss } of realLosses) {
        lines.push(`${sym}: -${_fmtMoney(Math.abs(loss))} unrealized loss`)
      }
      lines.push('')
    }
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

  // Cash drag — group same fund across multiple HBIL accounts by name prefix
  if (brief.cash_drag_alerts?.length) {
    // Group entries whose names share the same first ~40 chars (same fund, different accounts)
    const cashGroups = new Map()
    for (const p of brief.cash_drag_alerts) {
      const rawName = p.ticker || p.name || p.description || p.symbol || 'Cash'
      const groupKey = rawName.slice(0, 40).trimEnd().toLowerCase()
      if (!cashGroups.has(groupKey)) {
        cashGroups.set(groupKey, { name: rawName, total: 0, count: 0 })
      }
      const g = cashGroups.get(groupKey)
      g.total += p.market_value || 0
      g.count++
    }
    const cashList = [...cashGroups.values()].sort((a, b) => b.total - a.total)
    lines.push(`💵 CASH DRAG (${cashList.length} position${cashList.length !== 1 ? 's' : ''})`, '─'.repeat(20))
    for (const g of cashList) {
      const acctNote = g.count > 1 ? ` (${g.count} accounts)` : ''
      lines.push(`${g.name}: ${_fmtMoney(g.total)}${acctNote}`)
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
//
// options:
//   collectionId      — active collection ID
//   collection        — active collection object
//   groupId           — active group ID (or null) for group-aware commands
//   messages          — recent chat messages array (for /notes and /followup)
//   providerHeaders   — AI provider headers { 'x-ai-key': ..., etc. }
export const runSlashCommand = async (input, { collectionId, collection, groupId, messages, providerHeaders }) => {
  const cmd = input.trim().split(/\s+/)[0].toLowerCase()
  if (!SLASH_COMMANDS[cmd]) {
    return { cmd, content: `Unknown command: ${cmd}`, error: true }
  }

  try {
    if (cmd === '/help') {
      return { cmd, content: formatHelp() }
    }

    if (cmd === '/tools') {
      return { cmd, content: formatTools() }
    }

    if (cmd === '/brief') {
      if (groupId) {
        const response = await axios.post(`/api/groups/${groupId}/brief`)
        const brief = response.data
        const header = brief.group_name
          ? `HOUSEHOLD BRIEF — ${brief.group_name} (${brief.collection_count} account${brief.collection_count !== 1 ? 's' : ''})`
          : 'HOUSEHOLD BRIEF'
        return { cmd, content: header + '\n' + '═'.repeat(Math.min(header.length, 48)) + '\n\n' + formatBrief(brief) }
      }
      const response = await axios.post(`/api/collections/${collectionId}/brief`)
      return { cmd, content: formatBrief(response.data) }
    }

    if (cmd === '/notes') {
      const apiMessages = (messages || [])
        .filter(m => !m.streaming && m.content && !m.slashCommand)
        .slice(-30)
        .map(m => ({ role: m.role, content: m.content }))
      const response = await axios.post(
        `/api/collections/${collectionId}/notes`,
        { messages: apiMessages, provider: _inferProvider(providerHeaders) },
        { headers: providerHeaders || {} },
      )
      return { cmd, content: response.data.content }
    }

    if (cmd === '/followup') {
      const apiMessages = (messages || [])
        .filter(m => !m.streaming && m.content && !m.slashCommand)
        .slice(-30)
        .map(m => ({ role: m.role, content: m.content }))
      const response = await axios.post(
        `/api/collections/${collectionId}/followup`,
        { messages: apiMessages, provider: _inferProvider(providerHeaders) },
        { headers: providerHeaders || {} },
      )
      return { cmd, content: response.data.content }
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

function _inferProvider(headers) {
  if (!headers) return 'anthropic'
  if (headers['x-ollama-model'] || headers['x-ai-model']?.includes('llama') || headers['x-ai-model']?.includes('mistral')) return 'ollama'
  if (headers['x-openai-model']) return 'openai'
  return 'anthropic'
}
