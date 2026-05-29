import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  appendChatMessage,
  createChatSession,
  deleteChatSession,
  getChatSession,
  listChatSessions,
  renameChatSession,
} from '../utils/chatSessionApi'

const STORAGE_KEY = 'finn_chat_history_v2'
const LEGACY_STORAGE_KEY = 'finn_chat_history_v1'
const SCOPE_STORAGE_KEY = 'finn_chat_scope_v1'
const MAX_MESSAGES_PER_SESSION = 100
const MAX_SESSIONS_PER_COLLECTION = 50

// Slice B — server-side persistence.
// localStorage stays as the source of truth for live UX (instant writes, no
// network latency); the server is a durability + cross-device backup that
// mirrors mutations in the background. All server calls are fire-and-forget
// with silent failures so a flaky network never breaks chat.
const SERVER_SYNC_ENABLED = true

const newSessionId = () =>
  `s_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`

const makeSession = (overrides = {}) => ({
  id: newSessionId(),
  title: 'New chat',
  messages: [],
  createdAt: Date.now(),
  updatedAt: Date.now(),
  ...overrides,
})

const deriveTitle = (text) => {
  if (!text) return 'New chat'
  const trimmed = text.trim().replace(/\s+/g, ' ')
  return trimmed.length > 60 ? trimmed.slice(0, 57) + '…' : trimmed
}

export const useChatStore = defineStore('chat', () => {
  // { collectionId: { sessions: [Session], activeSessionId: string } }
  const conversations = ref({})

  // Per-document chat scope filter, persisted separately from conversations so
  // it survives session reset.
  // { [collectionId]: string[] | undefined }
  //   - undefined / missing → "all sources in scope" (no filter sent to server)
  //   - empty array → "no sources in scope" (the user explicitly unchecked everything)
  //   - non-empty array → only these document IDs are in scope for chat
  const scopedDocIds = ref({})

  const loadFromStorage = () => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY)
      if (saved) {
        conversations.value = JSON.parse(saved)
      } else {
        // Migrate from v1 format: { collectionId: { messages: [...] } }
        const legacy = localStorage.getItem(LEGACY_STORAGE_KEY)
        if (legacy) {
          const parsed = JSON.parse(legacy)
          const migrated = {}
          for (const [colId, conv] of Object.entries(parsed)) {
            const msgs = conv?.messages || []
            const session = makeSession({
              messages: msgs,
              title: deriveTitle(msgs.find((m) => m.role === 'user')?.content),
            })
            migrated[colId] = {
              sessions: [session],
              activeSessionId: session.id,
            }
          }
          conversations.value = migrated
          saveToStorage()
        }
      }

      const savedScope = localStorage.getItem(SCOPE_STORAGE_KEY)
      if (savedScope) {
        scopedDocIds.value = JSON.parse(savedScope) || {}
      }
    } catch (e) {
      console.error('Failed to load chat history:', e)
    }
  }

  const saveToStorage = () => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations.value))
    } catch (e) {
      console.error('Failed to save chat history:', e)
    }
  }

  const saveScope = () => {
    try {
      localStorage.setItem(SCOPE_STORAGE_KEY, JSON.stringify(scopedDocIds.value))
    } catch (e) {
      console.error('Failed to save chat scope:', e)
    }
  }

  // ── Server sync (Slice B) ───────────────────────────────────────────────
  //
  // Track which (collectionId, sessionId) pairs have been confirmed on the
  // server so we can lazy-create on first message instead of POSTing an
  // empty session every time the user opens a chat. Module-scoped, in-memory
  // only — server is the source of truth for "does this session exist".
  // { [collectionId]: Set<sessionId> }
  const serverKnownSessions = new Map()

  const isServerKnown = (collectionId, sessionId) => {
    return serverKnownSessions.get(collectionId)?.has(sessionId) || false
  }
  const markServerKnown = (collectionId, sessionId) => {
    if (!serverKnownSessions.has(collectionId)) {
      serverKnownSessions.set(collectionId, new Set())
    }
    serverKnownSessions.get(collectionId).add(sessionId)
  }
  const markServerUnknown = (collectionId, sessionId) => {
    serverKnownSessions.get(collectionId)?.delete(sessionId)
  }

  const ensureSessionOnServer = async (collectionId, sessionId) => {
    if (!SERVER_SYNC_ENABLED || !collectionId || !sessionId) return
    if (isServerKnown(collectionId, sessionId)) return
    const conv = conversations.value[collectionId]
    const session = conv?.sessions?.find((s) => s.id === sessionId)
    if (!session) return
    try {
      // create_session is idempotent server-side: existing id → returns
      // the stored row without overwriting. Safe to call eagerly.
      await createChatSession(collectionId, {
        sessionId: session.id,
        title: session.title === 'New chat' ? null : session.title,
      })
      markServerKnown(collectionId, sessionId)
    } catch (e) {
      // localStorage still authoritative — log and move on.
      console.warn('chat sync: ensureSessionOnServer failed', e)
    }
  }

  const persistMessageToServer = (collectionId, sessionId, message) => {
    if (!SERVER_SYNC_ENABLED || !collectionId || !sessionId || !message) return
    // Fire and forget. Awaited internally so ensure→append order is
    // preserved per message, but no caller blocks on this.
    void (async () => {
      try {
        await ensureSessionOnServer(collectionId, sessionId)
        await appendChatMessage(collectionId, sessionId, message)
      } catch (e) {
        console.warn('chat sync: persistMessageToServer failed', e)
      }
    })()
  }

  const persistDeleteToServer = (collectionId, sessionId) => {
    if (!SERVER_SYNC_ENABLED || !collectionId || !sessionId) return
    if (!isServerKnown(collectionId, sessionId)) return
    void (async () => {
      try {
        await deleteChatSession(collectionId, sessionId)
        markServerUnknown(collectionId, sessionId)
      } catch (e) {
        console.warn('chat sync: persistDeleteToServer failed', e)
      }
    })()
  }

  const persistRenameToServer = (collectionId, sessionId, title) => {
    if (!SERVER_SYNC_ENABLED || !collectionId || !sessionId) return
    void (async () => {
      try {
        await ensureSessionOnServer(collectionId, sessionId)
        await renameChatSession(collectionId, sessionId, title)
      } catch (e) {
        console.warn('chat sync: persistRenameToServer failed', e)
      }
    })()
  }

  // Pull sessions + messages from the server into local state. Merges:
  //   - sessions present on server but not locally → fetched and added.
  //   - sessions present in both → leave local untouched (stays the UX
  //     source of truth this turn; conflicts converge on next mutation).
  //   - sessions present locally but not on server → pushed up via the
  //     usual ensure-on-server / persist-message paths later.
  // Idempotent; safe to call on every collection switch.
  const hydrateFromServer = async (collectionId) => {
    if (!SERVER_SYNC_ENABLED || !collectionId) return
    let serverSessions
    try {
      serverSessions = await listChatSessions(collectionId, { limit: 100 })
    } catch (e) {
      console.warn('chat sync: hydrateFromServer list failed', e)
      return
    }

    ensureConversation(collectionId)
    const conv = conversations.value[collectionId]
    const localIds = new Set(conv.sessions.map((s) => s.id))

    for (const sess of serverSessions) {
      markServerKnown(collectionId, sess.id)
      if (localIds.has(sess.id)) continue
      try {
        const full = await getChatSession(collectionId, sess.id)
        const messages = (full.messages || []).map((m) => ({
          role: m.role,
          content: m.content,
          ...(m.metadata || {}),
        }))
        conv.sessions.push({
          id: sess.id,
          title: sess.title || 'New chat',
          messages,
          createdAt: Date.parse(sess.created_at) || Date.now(),
          updatedAt: Date.parse(sess.updated_at) || Date.now(),
        })
      } catch (e) {
        console.warn('chat sync: hydrateFromServer fetch failed', sess.id, e)
      }
    }
    // Sort sessions by updatedAt DESC so the most-recently-touched sits at
    // the top of the session picker — matches the server's list ordering.
    conv.sessions.sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0))
    if (!conv.activeSessionId && conv.sessions.length) {
      conv.activeSessionId = conv.sessions[0].id
    }
    saveToStorage()
  }

  // Returns the filter array, or null when no filter is set ("all sources in scope").
  const getScopedDocumentIds = (collectionId) => {
    const v = scopedDocIds.value[collectionId]
    return Array.isArray(v) ? v : null
  }

  const setScopedDocumentIds = (collectionId, ids) => {
    if (ids == null) {
      delete scopedDocIds.value[collectionId]
    } else {
      scopedDocIds.value[collectionId] = [...ids]
    }
    saveScope()
  }

  // Reconcile the saved filter against the currently-loaded document list.
  // Drops IDs for deleted docs; collapses to "all in scope" when the filter
  // covers every known doc. Called by SourcesSidebar after loadDocuments.
  const reconcileScope = (collectionId, allDocIds) => {
    const current = scopedDocIds.value[collectionId]
    if (!Array.isArray(current)) return  // already "all in scope"
    const known = new Set(allDocIds)
    const pruned = current.filter((id) => known.has(id))
    if (pruned.length === allDocIds.length) {
      // Filter now covers everything — collapse back to "all in scope" so a
      // future doc add doesn't silently start excluded.
      delete scopedDocIds.value[collectionId]
    } else {
      scopedDocIds.value[collectionId] = pruned
    }
    saveScope()
  }

  const ensureConversation = (collectionId) => {
    if (!conversations.value[collectionId]) {
      const session = makeSession()
      conversations.value[collectionId] = {
        sessions: [session],
        activeSessionId: session.id,
      }
    } else if (!conversations.value[collectionId].sessions?.length) {
      const session = makeSession()
      conversations.value[collectionId].sessions = [session]
      conversations.value[collectionId].activeSessionId = session.id
    } else if (!conversations.value[collectionId].activeSessionId) {
      conversations.value[collectionId].activeSessionId =
        conversations.value[collectionId].sessions[0].id
    }
  }

  const getActiveSession = (collectionId) => {
    ensureConversation(collectionId)
    const conv = conversations.value[collectionId]
    return conv.sessions.find((s) => s.id === conv.activeSessionId) || conv.sessions[0]
  }

  const getSessions = (collectionId) => {
    ensureConversation(collectionId)
    return conversations.value[collectionId].sessions
  }

  const getActiveSessionId = (collectionId) => {
    ensureConversation(collectionId)
    return conversations.value[collectionId].activeSessionId
  }

  const getMessages = (collectionId) => {
    return getActiveSession(collectionId)?.messages || []
  }

  const addMessage = (collectionId, message) => {
    const session = getActiveSession(collectionId)
    const stamped = { ...message, timestamp: Date.now() }
    session.messages.push(stamped)
    if (session.messages.length > MAX_MESSAGES_PER_SESSION) {
      session.messages = session.messages.slice(session.messages.length - MAX_MESSAGES_PER_SESSION)
    }
    // Auto-title from first user message
    if (message.role === 'user' && (session.title === 'New chat' || !session.title)) {
      session.title = deriveTitle(message.content)
    }
    session.updatedAt = Date.now()
    saveToStorage()
    persistMessageToServer(collectionId, session.id, stamped)
  }

  const addAssistantMessage = (collectionId, message, sources, aiUsage, structuredResults) => {
    const session = getActiveSession(collectionId)
    const stamped = {
      ...message,
      sources: sources || [],
      aiUsage: aiUsage || null,
      structuredResults: structuredResults || null,
      timestamp: Date.now(),
    }
    session.messages.push(stamped)
    session.updatedAt = Date.now()
    saveToStorage()
    persistMessageToServer(collectionId, session.id, stamped)
  }

  // --- Streaming support ---
  // Add a blank in-flight assistant message that will be mutated by SSE events.
  const addStreamingMessage = (collectionId) => {
    const session = getActiveSession(collectionId)
    session.messages.push({
      role: 'assistant',
      content: '',
      streaming: true,
      structuredResults: [],
      sources: [],
      citations: [],
      followups: [],
      aiUsage: null,
      timestamp: Date.now(),
    })
    session.updatedAt = Date.now()
    // Don't persist to storage while streaming — wait for finalizeStreamingMessage
  }

  const _lastAssistantMsg = (collectionId) => {
    const msgs = getActiveSession(collectionId)?.messages || []
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i].role === 'assistant') return msgs[i]
    }
    return null
  }

  const appendStreamingText = (collectionId, delta) => {
    const msg = _lastAssistantMsg(collectionId)
    if (msg) msg.content += delta
  }

  // Native-citation event from /api/chat/stream — Anthropic's structured
  // citations land here while text deltas are still streaming. Each citation
  // already carries the document_id / chunk_id mapping the engine resolved,
  // so the UI can deep-link to the source span without further lookup.
  const addStreamingCitation = (collectionId, citation) => {
    const msg = _lastAssistantMsg(collectionId)
    if (!msg) return
    if (!Array.isArray(msg.citations)) msg.citations = []
    msg.citations.push({
      // contentLength is captured at arrival time so the renderer can place
      // the inline pill where the model emitted the citation, even though
      // text continues to stream after this event.
      contentOffset: typeof msg.content === 'string' ? msg.content.length : 0,
      documentId: citation.document_id || null,
      chunkId: citation.chunk_id || null,
      pageNumber: citation.page_number || null,
      filename: citation.filename || null,
      citedText: citation.cited_text || '',
      startChar: citation.start_char ?? null,
      endChar: citation.end_char ?? null,
    })
  }

  // Perplexity-style "Related" suggestions — arrive after the answer has
  // streamed, just before the `done` event finalizes (and persists) the
  // message, so they ride along into storage without a separate write.
  const addStreamingFollowups = (collectionId, questions) => {
    const msg = _lastAssistantMsg(collectionId)
    if (msg) msg.followups = Array.isArray(questions) ? questions : []
  }

  const addStreamingToolCall = (collectionId, tool, args) => {
    const msg = _lastAssistantMsg(collectionId)
    if (msg) {
      if (!msg.structuredResults) msg.structuredResults = []
      msg.structuredResults.push({ tool, args, result: null, pending: true })
    }
  }

  const resolveStreamingToolCall = (collectionId, tool, result) => {
    const msg = _lastAssistantMsg(collectionId)
    if (!msg || !msg.structuredResults) return
    // Find the most recent pending entry for this tool and resolve it
    for (let i = msg.structuredResults.length - 1; i >= 0; i--) {
      const sr = msg.structuredResults[i]
      if (sr.tool === tool && sr.pending) {
        msg.structuredResults[i] = { tool, args: sr.args, ...result, pending: false }
        break
      }
    }
  }

  const addStreamingThinking = (collectionId, text) => {
    const msg = _lastAssistantMsg(collectionId)
    if (msg) {
      if (!msg.structuredResults) msg.structuredResults = []
      msg.structuredResults.push({ tool: '_thinking', args: {}, result: { text }, pending: false })
    }
  }

  const finalizeStreamingMessage = (collectionId, { sources, usage, structuredResults, content, slashCommand } = {}) => {
    const msg = _lastAssistantMsg(collectionId)
    if (!msg) return
    msg.streaming = false
    if (sources) msg.sources = sources
    if (usage) msg.aiUsage = { synthesis: { ...usage }, total_input_tokens: usage.input_tokens || 0, total_output_tokens: usage.output_tokens || 0 }
    // Replace structuredResults with the server's authoritative list if provided
    if (structuredResults && structuredResults.length > 0) msg.structuredResults = structuredResults
    // Optional content overwrite — used by /notes and /followup so the saved
    // message is the redacted final, not the raw streamed preview.
    if (typeof content === 'string') msg.content = content
    if (slashCommand) msg.slashCommand = slashCommand
    const session = getActiveSession(collectionId)
    if (session) session.updatedAt = Date.now()
    saveToStorage()
    // Server sync only fires here — not while the message is streaming —
    // so we persist a single complete assistant turn instead of a stream
    // of in-flight states.
    if (session) persistMessageToServer(collectionId, session.id, msg)
  }

  const removeLastStreamingMessage = (collectionId) => {
    const session = getActiveSession(collectionId)
    if (!session) return
    const msgs = session.messages
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i].role === 'assistant' && msgs[i].streaming) {
        msgs.splice(i, 1)
        break
      }
    }
    session.updatedAt = Date.now()
  }

  const clearMessages = (collectionId) => {
    const session = getActiveSession(collectionId)
    if (session) {
      session.messages = []
      session.title = 'New chat'
      session.updatedAt = Date.now()
      saveToStorage()
    }
  }

  const newSession = (collectionId) => {
    ensureConversation(collectionId)
    const conv = conversations.value[collectionId]

    // If the active session is already empty, just reuse it instead of piling up empties
    const active = conv.sessions.find((s) => s.id === conv.activeSessionId)
    if (active && active.messages.length === 0) {
      saveToStorage()
      return active.id
    }

    const session = makeSession()
    conv.sessions.unshift(session)
    conv.activeSessionId = session.id

    // Trim oldest sessions if over the cap
    if (conv.sessions.length > MAX_SESSIONS_PER_COLLECTION) {
      conv.sessions = conv.sessions.slice(0, MAX_SESSIONS_PER_COLLECTION)
    }
    saveToStorage()
    return session.id
  }

  const selectSession = (collectionId, sessionId) => {
    ensureConversation(collectionId)
    const conv = conversations.value[collectionId]
    if (conv.sessions.some((s) => s.id === sessionId)) {
      conv.activeSessionId = sessionId
      saveToStorage()
    }
  }

  const deleteSession = (collectionId, sessionId) => {
    const conv = conversations.value[collectionId]
    if (!conv) return
    conv.sessions = conv.sessions.filter((s) => s.id !== sessionId)
    if (conv.sessions.length === 0) {
      const session = makeSession()
      conv.sessions = [session]
      conv.activeSessionId = session.id
    } else if (conv.activeSessionId === sessionId) {
      conv.activeSessionId = conv.sessions[0].id
    }
    saveToStorage()
    persistDeleteToServer(collectionId, sessionId)
  }

  const renameSession = (collectionId, sessionId, title) => {
    const conv = conversations.value[collectionId]
    if (!conv) return
    const session = conv.sessions.find((s) => s.id === sessionId)
    if (session) {
      const nextTitle = deriveTitle(title) || 'Untitled'
      session.title = nextTitle
      session.updatedAt = Date.now()
      saveToStorage()
      persistRenameToServer(collectionId, sessionId, nextTitle)
    }
  }

  const clearCollectionChat = (collectionId) => {
    // Best-effort: delete the server-side sessions for this collection too,
    // so a "clear" survives a refresh. Local state is the source of truth
    // for the UX; server sync is fire-and-forget.
    const conv = conversations.value[collectionId]
    const knownIds = conv?.sessions?.map((s) => s.id) || []
    delete conversations.value[collectionId]
    saveToStorage()
    for (const sid of knownIds) {
      persistDeleteToServer(collectionId, sid)
    }
  }

  // Initialize from storage
  loadFromStorage()

  return {
    conversations,
    scopedDocIds,
    getMessages,
    getSessions,
    getActiveSessionId,
    getActiveSession,
    addMessage,
    addAssistantMessage,
    clearMessages,
    newSession,
    selectSession,
    deleteSession,
    renameSession,
    clearCollectionChat,
    // Per-document scope filter
    getScopedDocumentIds,
    setScopedDocumentIds,
    reconcileScope,
    // Streaming
    addStreamingMessage,
    appendStreamingText,
    addStreamingCitation,
    addStreamingFollowups,
    addStreamingToolCall,
    resolveStreamingToolCall,
    addStreamingThinking,
    finalizeStreamingMessage,
    removeLastStreamingMessage,
    // Server sync (Slice B)
    hydrateFromServer,
  }
})
