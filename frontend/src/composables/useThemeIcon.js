import { ref, computed, onMounted, onBeforeUnmount } from 'vue'

// Track the document's effective color scheme so theme-paired assets (logo
// light vs dark, hero light vs dark, etc.) can swap when the user picks a
// different daisyUI theme. We rely on each daisy theme setting `color-scheme`
// on :root — true for every theme in the daisyUI catalog — rather than a
// hardcoded list of theme names.
//
// Updates fire when:
//   - the active theme is switched (daisyUI flips data-theme on <html>)
//   - the OS color scheme changes (themes that follow `system`)
const scheme = ref('light')
let observer = null
let mq = null
let refCount = 0

function readScheme() {
  if (typeof document === 'undefined') return 'light'
  const cs = getComputedStyle(document.documentElement).colorScheme || ''
  // color-scheme can be a list ("light dark"); take the first concrete value
  if (cs.includes('dark') && !cs.includes('light')) return 'dark'
  if (cs.includes('light') && !cs.includes('dark')) return 'light'
  // ambiguous — fall back to the OS preference
  if (typeof window !== 'undefined' && window.matchMedia?.('(prefers-color-scheme: dark)').matches) {
    return 'dark'
  }
  return 'light'
}

function startWatching() {
  if (refCount++ > 0) return
  scheme.value = readScheme()
  observer = new MutationObserver(() => { scheme.value = readScheme() })
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['data-theme', 'class', 'style'],
  })
  mq = window.matchMedia('(prefers-color-scheme: dark)')
  mq.addEventListener('change', () => { scheme.value = readScheme() })
}

function stopWatching() {
  if (--refCount > 0) return
  observer?.disconnect(); observer = null
  // matchMedia listener is auto-cleaned when the page unloads; no removeEventListener
  // needed because we keep the same closure across the app lifetime.
}

// Resolve a path against the build's BASE_URL so absolute "/foo.svg" references
// to public/ assets work under both http:// (web build, base "/") and file://
// (Electron build, base "./"). Without this, Electron's loadFile() turns "/foo"
// into a filesystem-root lookup and the asset 404s.
function resolvePublic(p) {
  if (typeof p !== 'string') return p
  if (p.startsWith('/') && !p.startsWith('//')) {
    return import.meta.env.BASE_URL + p.slice(1)
  }
  return p
}

/**
 * Pick between two assets based on the active theme's color scheme.
 *
 * @param {string} lightSrc URL to use when color-scheme is "light"
 * @param {string} darkSrc  URL to use when color-scheme is "dark"
 * @returns {{ src: import('vue').ComputedRef<string>, scheme: import('vue').Ref<string> }}
 */
export function useThemeIcon(lightSrc, darkSrc) {
  onMounted(startWatching)
  onBeforeUnmount(stopWatching)
  const light = resolvePublic(lightSrc)
  const dark = resolvePublic(darkSrc)
  const src = computed(() => (scheme.value === 'dark' ? dark : light))
  return { src, scheme }
}

/**
 * Track the active color scheme without picking an asset — for callers that
 * need to feed `'light'`/`'dark'` into a JS API (chart themes, canvas colors)
 * rather than swap an <img src>.
 *
 * @returns {{ scheme: import('vue').Ref<'light' | 'dark'> }}
 */
export function useColorScheme() {
  onMounted(startWatching)
  onBeforeUnmount(stopWatching)
  return { scheme }
}
