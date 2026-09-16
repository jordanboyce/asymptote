// Escape document text before adding our own markup. One pass avoids matches
// inside HTML attributes or inside marks produced for earlier query terms.
export function highlightSnippet(value, query) {
  const text = String(value)
  const escape = value => value.replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]))
  const words = [...new Set(String(query || '').split(/\s+/).filter(word => word.length > 2))]
  if (!words.length) return escape(text)
  const pattern = new RegExp(words.sort((a, b) => b.length - a.length).map(word => word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|'), 'gi')
  let html = '', end = 0
  for (const match of text.matchAll(pattern)) {
    html += escape(text.slice(end, match.index)) + `<mark class="bg-warning/25 text-base-content rounded-sm px-0.5">${escape(match[0])}</mark>`
    end = match.index + match[0].length
  }
  return html + escape(text.slice(end))
}
