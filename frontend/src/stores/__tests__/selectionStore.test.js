import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useSelectionStore } from '../selectionStore'
import { useCollectionStore } from '../collectionStore'

describe('selectionStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    sessionStorage.clear()
    useCollectionStore().currentCollectionId = 'c1'
  })

  it('is empty by default and reports inactive', () => {
    const s = useSelectionStore()
    expect(s.currentIds).toEqual([])
    expect(s.active).toBe(false)
  })

  it('toggles, dedupes and clears for the current collection', () => {
    const s = useSelectionStore()
    s.toggle('a')
    s.toggle('b')
    s.toggle('a')
    expect(s.currentIds).toEqual(['b'])
    s.set(['x', 'x', 'y'])
    expect(s.currentIds).toEqual(['x', 'y'])
    expect(s.count).toBe(2)
    s.clear()
    expect(s.active).toBe(false)
  })

  it('keeps a separate selection per collection', () => {
    const s = useSelectionStore()
    const collections = useCollectionStore()
    s.set(['a'])
    collections.currentCollectionId = 'c2'
    expect(s.currentIds).toEqual([])
    s.set(['z'])
    collections.currentCollectionId = 'c1'
    expect(s.currentIds).toEqual(['a'])
    expect(s.idsFor('c2')).toEqual(['z'])
  })

  it('drops deleted documents and persists to sessionStorage', () => {
    const s = useSelectionStore()
    s.set(['a', 'b', 'c'])
    s.remove(['b'])
    expect(s.currentIds).toEqual(['a', 'c'])
    const saved = JSON.parse(sessionStorage.getItem('asymptote_source_selection'))
    expect(saved).toEqual({ c1: ['a', 'c'] })
  })
})
