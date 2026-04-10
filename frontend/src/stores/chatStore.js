import { defineStore } from 'pinia'
import { ref } from 'vue'

const STORAGE_KEY = 'asymptote_chat_history_v2'
const LEGACY_STORAGE_KEY = 'asymptote_chat_history_v1'
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

  const loadFromStorage = () => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY)
      if (saved) {
        conversations.value = JSON.parse(saved)
        return
      }
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

  const addAssistantMessage = (collectionId, message, sources, aiUsage) => {
    const session = getActiveSession(collectionId)
    session.messages.push({
      ...message,
      sources: sources || [],
      aiUsage: aiUsage || null,
      timestamp: Date.now(),
    })
    session.updatedAt = Date.now()
    saveToStorage()
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
  }
})
