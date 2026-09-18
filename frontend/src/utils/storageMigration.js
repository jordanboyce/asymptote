// One-time carry-over of browser state from the Asymptote-era key names.
//
// The rename to Clio changed every localStorage / sessionStorage key from
// `asymptote_*` to `clio_*`. Without this, the first load after an upgrade
// would silently drop chat history, the current collection, search settings,
// provider selection and per-surface overrides. Run once per storage area,
// before any store reads: copy each old key to its new name when the new
// name is still empty, then drop the old key so it cannot resurrect a value
// the person has since cleared.

const OLD_PREFIX = 'asymptote_'
const NEW_PREFIX = 'clio_'
const MARKER = 'clio_storage_migrated'

export function migrateStorage(storage) {
  let migrated = 0
  try {
    if (!storage || storage.getItem(MARKER)) return 0
    const oldKeys = []
    for (let i = 0; i < storage.length; i++) {
      const key = storage.key(i)
      if (key && key.startsWith(OLD_PREFIX)) oldKeys.push(key)
    }
    for (const oldKey of oldKeys) {
      const newKey = NEW_PREFIX + oldKey.slice(OLD_PREFIX.length)
      if (storage.getItem(newKey) === null) {
        storage.setItem(newKey, storage.getItem(oldKey))
        migrated++
      }
      storage.removeItem(oldKey)
    }
    storage.setItem(MARKER, '1')
  } catch {
    // Private windows and blocked site data throw; nothing to carry over.
  }
  return migrated
}

export function migrateBrowserStorage() {
  let total = 0
  try { total += migrateStorage(window.localStorage) } catch { /* unavailable */ }
  try { total += migrateStorage(window.sessionStorage) } catch { /* unavailable */ }
  return total
}
