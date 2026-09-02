import { ref } from 'vue'

// Native <dialog> lifecycle with focus restore.
//
// Most dialogs in the app used to toggle DaisyUI's `modal-open` class on a
// <dialog> element without ever calling showModal() — which renders fine
// but silently loses everything the native modal path provides: the focus
// trap, Escape-to-close, and the inert background (screen readers could
// still read and reach content behind the dialog). This composable wraps
// the real API and adds the one thing the browser doesn't do: putting
// focus back where it was when the dialog closes.
//
// Usage:
//   const modal = useModal()
//   <dialog ref="modal.dialogRef" class="modal" @close="modal.onClosed">…
//   modal.open() / modal.close(); v-if content on modal.isOpen as needed.
export function useModal({ onClose } = {}) {
  const dialogRef = ref(null)
  const isOpen = ref(false)
  let previouslyFocused = null

  function open() {
    const el = dialogRef.value
    if (!el || isOpen.value) return
    previouslyFocused = document.activeElement
    el.showModal()
    isOpen.value = true
  }

  function close() {
    const el = dialogRef.value
    if (el && el.open) el.close() // fires the native 'close' event → onClosed
  }

  // Bind to the <dialog>'s native `close` event so Escape (which closes the
  // dialog without going through close()) still restores focus and state.
  function onClosed() {
    isOpen.value = false
    if (previouslyFocused && typeof previouslyFocused.focus === 'function') {
      previouslyFocused.focus()
    }
    previouslyFocused = null
    onClose?.()
  }

  return { dialogRef, isOpen, open, close, onClosed }
}
