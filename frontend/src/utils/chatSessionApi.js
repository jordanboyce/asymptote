// Slice B — server-side chat session persistence.
//
// Thin axios wrappers around /api/collections/{id}/chat/sessions[/{sid}[/messages]].
// Errors are surfaced to the caller; in chatStore the call sites swallow them
// because localStorage is still the source of truth and server sync is
// bonus durability. If we promote the server to primary later, callers can
// stop swallowing and start handling.

import axios from 'axios'

export async function listChatSessions(collectionId, { includeArchived = false, limit = 50 } = {}) {
  const { data } = await axios.get(`/api/collections/${collectionId}/chat/sessions`, {
    params: { include_archived: includeArchived, limit },
  })
  return data.sessions || []
}

export async function createChatSession(collectionId, { title, sessionId } = {}) {
  const body = {}
  if (title != null) body.title = title
  if (sessionId != null) body.session_id = sessionId
  const { data } = await axios.post(`/api/collections/${collectionId}/chat/sessions`, body)
  return data
}

export async function getChatSession(collectionId, sessionId) {
  const { data } = await axios.get(
    `/api/collections/${collectionId}/chat/sessions/${sessionId}`,
  )
  return data
}

export async function renameChatSession(collectionId, sessionId, title) {
  const { data } = await axios.patch(
    `/api/collections/${collectionId}/chat/sessions/${sessionId}`,
    { title },
  )
  return data
}

export async function deleteChatSession(collectionId, sessionId) {
  await axios.delete(`/api/collections/${collectionId}/chat/sessions/${sessionId}`)
}

// Persist one message. The frontend keeps richer per-message state than the
// server model needs (e.g. ephemeral `streaming: true` flags); strip those
// to a clean role/content/metadata envelope on the way out.
export async function appendChatMessage(collectionId, sessionId, message) {
  const { role, content, ...rest } = message
  // The metadata blob is anything the renderer needs to rebuild the message
  // verbatim: sources, citations, tool calls, aiUsage, slashCommand, etc.
  // The server stores it as opaque JSON; the client owns the schema.
  const metadata = {}
  for (const key of ['sources', 'citations', 'structuredResults', 'aiUsage', 'slashCommand', 'timestamp']) {
    if (rest[key] !== undefined && rest[key] !== null) {
      metadata[key] = rest[key]
    }
  }
  const body = {
    role,
    content: typeof content === 'string' ? content : String(content ?? ''),
    metadata: Object.keys(metadata).length ? metadata : null,
  }
  const { data } = await axios.post(
    `/api/collections/${collectionId}/chat/sessions/${sessionId}/messages`,
    body,
  )
  return data
}
