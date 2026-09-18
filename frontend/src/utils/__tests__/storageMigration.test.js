import { describe, it, expect, beforeEach } from 'vitest'
import { migrateStorage } from '../storageMigration'

describe('migrateStorage', () => {
  beforeEach(() => localStorage.clear())

  it('copies asymptote_* keys to clio_* and removes the old ones', () => {
    localStorage.setItem('asymptote_current_collection', 'abc')
    localStorage.setItem('asymptote_chat_history_v2', '[1]')
    localStorage.setItem('ollama_model', 'gemma')
    expect(migrateStorage(localStorage)).toBe(2)
    expect(localStorage.getItem('clio_current_collection')).toBe('abc')
    expect(localStorage.getItem('clio_chat_history_v2')).toBe('[1]')
    expect(localStorage.getItem('asymptote_current_collection')).toBeNull()
    expect(localStorage.getItem('ollama_model')).toBe('gemma')
  })

  it('never overwrites a value already stored under the new name', () => {
    localStorage.setItem('asymptote_search_mode', 'old')
    localStorage.setItem('clio_search_mode', 'new')
    expect(migrateStorage(localStorage)).toBe(0)
    expect(localStorage.getItem('clio_search_mode')).toBe('new')
    expect(localStorage.getItem('asymptote_search_mode')).toBeNull()
  })

  it('runs only once per storage area', () => {
    localStorage.setItem('asymptote_default_top_k', '5')
    migrateStorage(localStorage)
    localStorage.removeItem('clio_default_top_k')
    localStorage.setItem('asymptote_default_top_k', '9')
    expect(migrateStorage(localStorage)).toBe(0)
    expect(localStorage.getItem('clio_default_top_k')).toBeNull()
  })
})
