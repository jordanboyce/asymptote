import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { useUiStore } from '../uiStore'
import { useProviderStore } from '../providerStore'
import { useCollectionStore } from '../collectionStore'

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
})

describe('uiStore toasts', () => {
  it('adds, auto-dismisses, and manually dismisses', () => {
    vi.useFakeTimers()
    const ui = useUiStore()

    const id = ui.notify('saved', 'success', { duration: 1000 })
    expect(ui.toasts).toHaveLength(1)
    expect(ui.toasts[0]).toMatchObject({ id, message: 'saved', type: 'success' })

    vi.advanceTimersByTime(1100)
    expect(ui.toasts).toHaveLength(0)

    const stay = ui.notify('sticky', 'info', { duration: 0 })
    vi.advanceTimersByTime(60000)
    expect(ui.toasts).toHaveLength(1)
    ui.dismiss(stay)
    expect(ui.toasts).toHaveLength(0)
    vi.useRealTimers()
  })

  it('toastError normalizes anything a catch block receives', () => {
    const ui = useUiStore()
    ui.toastError({ message: 'normalized message' })
    ui.toastError({ response: { data: { detail: 'axios detail' } } })
    ui.toastError(undefined, 'fallback text')
    expect(ui.toasts.map((t) => t.message)).toEqual([
      'normalized message',
      'axios detail',
      'fallback text',
    ])
    expect(ui.toasts.every((t) => t.type === 'error')).toBe(true)
  })
})

describe('providerStore reactivity', () => {
  it('bumps version through notifyProviderChange', async () => {
    const store = useProviderStore()
    const before = store.version
    const { notifyProviderChange } = await import('../../utils/aiProviders')
    notifyProviderChange()
    expect(store.version).toBe(before + 1)
  })

  it('re-lists configured providers after a config write', async () => {
    const store = useProviderStore()
    expect(store.configuredIds).toEqual([])
    const { upsertProviderConfig, setActiveProviderLS } = await import('../../utils/aiProviders')
    upsertProviderConfig('anthropic', { apiKey: 'sk-test' })
    setActiveProviderLS('anthropic') // write path fires notifyProviderChange
    expect(store.configuredIds).toContain('anthropic')
    expect(store.activeProviderId).toBe('anthropic')
  })
})

describe('collectionStore permissions', () => {
  it.each([
    ['owner', true],
    ['readwrite', true],
    ['read', false],
    [undefined, true], // no permission field = owner (shared appliance)
  ])('permission %s → canEditCurrent %s', (permission, expected) => {
    const store = useCollectionStore()
    store.collections = [{ id: 'c1', name: 'C1', permission }]
    store.currentCollectionId = 'c1'
    expect(store.canEditCurrent).toBe(expected)
  })

  // The contributor/owner split: a read-write share may add sources but may
  // not change how the collection is built. Mirrors api/deps.require_collection_access.
  it.each([
    ['owner', true],
    ['readwrite', false],
    ['read', false],
    [undefined, true], // no permission field = owner (shared appliance)
  ])('permission %s → canConfigureCurrent %s', (permission, expected) => {
    const store = useCollectionStore()
    store.collections = [{ id: 'c1', name: 'C1', permission }]
    store.currentCollectionId = 'c1'
    expect(store.canConfigureCurrent).toBe(expected)
    expect(store.canConfigure(store.currentCollection)).toBe(expected)
  })

  it('flags a published collection a reader is looking at', () => {
    const store = useCollectionStore()
    store.collections = [{ id: 'pub', name: 'Handbook', permission: 'read', published: true }]
    store.currentCollectionId = 'pub'
    expect(store.isReadOnlyCurrent).toBe(true)
    expect(store.currentIsPublished).toBe(true)
    expect(store.canEditCurrent).toBe(false)
    expect(store.canConfigureCurrent).toBe(false)
  })

  it("keeps the owner's own published collection fully editable", () => {
    const store = useCollectionStore()
    store.collections = [{ id: 'pub', name: 'Handbook', permission: 'owner', published: true }]
    store.currentCollectionId = 'pub'
    expect(store.currentIsPublished).toBe(true)
    expect(store.isReadOnlyCurrent).toBe(false)
    expect(store.canConfigureCurrent).toBe(true)
  })

  it('groups shared collections after owned ones', () => {
    const store = useCollectionStore()
    store.collections = [
      { id: 'shared1', name: 'A shared', shared: true },
      { id: 'default', name: 'Default' },
      { id: 'mine', name: 'Mine' },
    ]
    expect(store.sortedCollections.map((c) => c.id)).toEqual(['default', 'mine', 'shared1'])
    expect(store.sharedCollections.map((c) => c.id)).toEqual(['shared1'])
  })
})

describe('http interceptor offline detection', () => {
  const rejected = async (error) => {
    const { http } = await import('../../utils/http')
    const handler = http.interceptors.response.handlers[0].rejected
    return handler(error).catch((e) => e)
  }

  it('flags offline on network-layer failure and on gateway errors', async () => {
    const ui = useUiStore()
    let err = await rejected({}) // no response = network layer
    expect(err.isNetwork).toBe(true)
    expect(ui.offline).toBe(true)

    ui.offline = false
    err = await rejected({ response: { status: 502, statusText: 'Bad Gateway' } })
    expect(err.isNetwork).toBe(true)
    expect(ui.offline).toBe(true)
  })

  it('clears offline and normalizes an ordinary API error', async () => {
    const ui = useUiStore()
    ui.offline = true
    const err = await rejected({
      response: { status: 404, statusText: 'Not Found', data: { detail: 'Collection not found' } },
    })
    expect(ui.offline).toBe(false)
    expect(err).toMatchObject({ status: 404, message: 'Collection not found', isRateLimit: false })
  })

  it('turns a 429 into the shared retry contract plus one toast', async () => {
    const ui = useUiStore()
    const err = await rejected({
      response: { status: 429, data: { detail: 'Rate limit reached', retry_after_seconds: 12 } },
    })
    expect(err).toMatchObject({ isRateLimit: true, retryAfterSeconds: 12 })
    expect(ui.toasts.some((t) => t.message.includes('Rate limit'))).toBe(true)
  })
})
