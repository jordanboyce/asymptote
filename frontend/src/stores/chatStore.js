import { defineStore } from 'pinia'
import { ref } from 'vue'

const STORAGE_KEY = 'asymptote_chat_history_v2'
const LEGACY_STORAGE_KEY = 'asymptote_chat_history_v1'
const SCOPE_STORAGE_KEY = 'asymptote_chat_scope_v1'
const MAX_MESSAGES_PER_SESSION = 100
const MAX_SESSIONS_PER_COLLECTION = 50

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
    session.messages.push({ ...message, timestamp: Date.now() })
    if (session.messages.length > MAX_MESSAGES_PER_SESSION) {
      session.messages = session.messages.slice(session.messages.length - MAX_MESSAGES_PER_SESSION)
    }
    // Auto-title from first user message
    if (message.role === 'user' && (session.title === 'New chat' || !session.title)) {
      session.title = deriveTitle(message.content)
    }
    session.updatedAt = Date.now()
    saveToStorage()
  }

  const addAssistantMessage = (collectionId, message, sources, aiUsage, structuredResults) => {
    const session = getActiveSession(collectionId)
    session.messages.push({
      ...message,
      sources: sources || [],
      aiUsage: aiUsage || null,
      structuredResults: structuredResults || null,
      timestamp: Date.now(),
    })
    session.updatedAt = Date.now()
    saveToStorage()
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
  }

  const renameSession = (collectionId, sessionId, title) => {
    const conv = conversations.value[collectionId]
    if (!conv) return
    const session = conv.sessions.find((s) => s.id === sessionId)
    if (session) {
      session.title = deriveTitle(title) || 'Untitled'
      session.updatedAt = Date.now()
      saveToStorage()
    }
  }

  const clearCollectionChat = (collectionId) => {
    delete conversations.value[collectionId]
    saveToStorage()
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
    addStreamingToolCall,
    resolveStreamingToolCall,
    addStreamingThinking,
    finalizeStreamingMessage,
    removeLastStreamingMessage,
  }
})
