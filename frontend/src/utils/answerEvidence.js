import { marked } from 'marked'
import DOMPurify from 'dompurify'

// Source links stay on this deployment, even when history contains an old host.
export function sourceHref(source) {
  try {
    const url = new URL(source.page_url || source.pdf_url, window.location.origin)
    const expected = `/documents/${encodeURIComponent(source.document_id)}/pdf`
    if (url.pathname !== expected || !['http:', 'https:'].includes(url.protocol)) return ''
    return `${url.pathname}${url.search}${url.hash}`
  } catch { return '' }
}

export function renderEvidenceMarkdown(text, sources = []) {
  const root = document.createElement('div')
  root.innerHTML = DOMPurify.sanitize(marked.parse(String(text || ''), { gfm: true, breaks: true }), {
    FORBID_TAGS: ['button', 'input', 'form', 'textarea', 'select'],
    ALLOW_DATA_ATTR: false,
  })
  // Generate controls only from prose text nodes; never rewrite HTML attributes,
  // code samples, or links supplied by the model.
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  const nodes = []
  while (walker.nextNode()) nodes.push(walker.currentNode)
  for (const node of nodes) {
    if (node.parentElement.closest('a, code, pre, button')) continue
    const pattern = /\[Source\s+(\d+)\]/gi
    const fragment = document.createDocumentFragment()
    let end = 0
    for (const match of node.textContent.matchAll(pattern)) {
      fragment.append(node.textContent.slice(end, match.index))
      const number = Number(match[1])
      if (number > 0 && number <= sources.length) {
        const button = document.createElement('button')
        button.type = 'button'
        button.className = 'link link-primary font-medium px-1 rounded-sm'
        button.dataset.sourceNumber = String(number)
        button.setAttribute('aria-label', `Review source ${number}: ${sources[number - 1].filename}`)
        button.textContent = match[0]
        fragment.append(button)
      } else {
        fragment.append(match[0])
      }
      end = match.index + match[0].length
    }
    if (end) {
      fragment.append(node.textContent.slice(end))
      node.replaceWith(fragment)
    }
  }
  for (const link of root.querySelectorAll('a')) {
    link.target = '_blank'
    link.rel = 'noopener noreferrer'
  }
  return root.innerHTML
}

export function answerWithReferences(message) {
  const references = (message.sources || []).map((source, index) => {
    const href = sourceHref(source)
    const location = source.page_number ? `, page / section ${source.page_number}` : ''
    const url = href ? `\n${new URL(href, window.location.origin).href}` : ''
    return `[Source ${index + 1}] ${source.filename}${location}${url}\n${source.text_snippet || ''}`
  })
  return references.length
    ? `${message.content}\n\nRetrieved evidence (review before relying on the answer):\n\n${references.join('\n\n')}`
    : message.content
}
