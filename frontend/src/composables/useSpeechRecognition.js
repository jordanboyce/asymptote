import { ref, onBeforeUnmount } from 'vue'

export function useSpeechRecognition() {
  const SpeechRecognitionClass =
    typeof window !== 'undefined'
      ? window.SpeechRecognition || window.webkitSpeechRecognition
      : null

  const supported = ref(!!SpeechRecognitionClass)
  const listening = ref(false)
  const error = ref('')

  let recognition = null
  let onTranscript = null

  const ensureInstance = () => {
    if (!supported.value || recognition) return
    recognition = new SpeechRecognitionClass()
    recognition.continuous = true
    recognition.interimResults = false
    recognition.lang = (typeof navigator !== 'undefined' && navigator.language) || 'en-US'

    recognition.onresult = (event) => {
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i]
        if (result.isFinal && onTranscript) {
          onTranscript(result[0].transcript)
        }
      }
    }
    recognition.onerror = (event) => {
      error.value = event.error || 'speech-recognition-error'
      listening.value = false
    }
    recognition.onend = () => {
      listening.value = false
    }
  }

  const start = (callback) => {
    if (!supported.value) {
      error.value = 'not-supported'
      return
    }
    onTranscript = callback
    error.value = ''
    ensureInstance()
    try {
      recognition.start()
      listening.value = true
    } catch (e) {
      error.value = String(e?.message || e)
    }
  }

  const stop = () => {
    if (recognition && listening.value) {
      try { recognition.stop() } catch {}
    }
    listening.value = false
  }

  const toggle = (callback) => {
    if (listening.value) stop()
    else start(callback)
  }

  onBeforeUnmount(() => {
    if (recognition) {
      recognition.onresult = null
      recognition.onerror = null
      recognition.onend = null
      try { recognition.abort() } catch {}
      recognition = null
    }
  })

  return { supported, listening, error, start, stop, toggle }
}
