import { defineAsyncComponent, h } from 'vue'
import LazyViewFallback from '../components/LazyViewFallback.vue'

/**
 * A view that ships in its own chunk.
 *
 * Hashed asset names plus an immutable cache mean a tab left open across a
 * deploy asks the new server for a chunk that no longer exists. Vue's default
 * for that is to render nothing at all, so the tab looks broken rather than
 * stale — which is exactly how it was reported. One retry covers a dropped
 * connection; past that the pane says what happened and offers the reload
 * that actually fixes it.
 */
export function lazyView(loader) {
  return defineAsyncComponent({
    loader,
    loadingComponent: () => h(LazyViewFallback),
    errorComponent: () => h(LazyViewFallback, { failed: true }),
    delay: 150,
    timeout: 20000,
    onError(error, retry, fail, attempts) {
      if (attempts <= 1) retry()
      else fail(error)
    },
  })
}
