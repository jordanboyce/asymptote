import { defineStore } from 'pinia'
import { ref } from 'vue'

const STORAGE_KEY = 'asymptote_chat_history_v1'
const MAX_MESSAGES_PER_CONVERSATION = 100

export const useChatStore = defineStore('chat', () => {
  // { collectionId: { messages: [{role, content, sources?, aiUsage?, timestamp}] } }
  const conversations = ref({})

  const loadFromStorage = () => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY)
      if (saved) {
        conversations.value = JSON.parse(saved)
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
      conversations.value[collectionId] = { messages: [] }
    }
  }

  const getMessages = (collectionId) => {
    return conversations.value[collectionId]?.messages || []
  }

  const addMessage = (collectionId, message) => {
    ensureConversation(collectionId)
    conversations.value[collectionId].messages.push({
      ...message,
      timestamp: Date.now(),
    })
    // Trim if too long
    const msgs = conversations.value[collectionId].messages
    if (msgs.length > MAX_MESSAGES_PER_CONVERSATION) {
      conversations.value[collectionId].messages = msgs.slice(msgs.length - MAX_MESSAGES_PER_CONVERSATION)
    }
    saveToStorage()
  }

  const addAssistantMessage = (collectionId, message, sources, aiUsage) => {
    ensureConversation(collectionId)
    conversations.value[collectionId].messages.push({
      ...message,
      sources: sources || [],
      aiUsage: aiUsage || null,
      timestamp: Date.now(),
    })
    saveToStorage()
  }

  const clearMessages = (collectionId) => {
    if (conversations.value[collectionId]) {
      conversations.value[collectionId].messages = []
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
    addMessage,
    addAssistantMessage,
    clearMessages,
    clearCollectionChat,
  }
})
