// Module-level singleton state so the header recording button (App.vue) and
// the sidebar entry (SourcesSidebar.vue) share one recording session — see
// §7 of advisor-desktop-ux/tasks.md. Component instances all read/write the
// same refs; there is intentionally no per-component factory.

import { ref, computed } from 'vue'
import axios from 'axios'

export const SUPPORTED_AUDIO_EXTENSIONS = [
  'mp3', 'wav', 'm4a', 'webm', 'ogg', 'flac', 'mp4', 'mpeg', 'mpga',
]

// Common audio formats users might try that the local Whisper pipeline does
// not handle — used so the upload error names the right thing.
const KNOWN_UNSUPPORTED_AUDIO_EXTENSIONS = [
  'aac', 'wma', 'aiff', 'aif', 'amr', 'opus', 'ac3',
]

const isRecording = ref(false)
const transcribing = ref(false)
const transcribeStatus = ref('')
const recordError = ref('')
const elapsedSeconds = ref(0)

let mediaRecorder = null
let recordedChunks = []
let mediaStream = null
let elapsedTimer = null
let cancelled = false

const formattedElapsed = computed(() => {
  const mm = String(Math.floor(elapsedSeconds.value / 60)).padStart(2, '0')
  const ss = String(elapsedSeconds.value % 60).padStart(2, '0')
  return `${mm}:${ss}`
})

function pickRecordingMime() {
  const candidates = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/ogg;codecs=opus',
    'audio/mp4',
  ]
  if (typeof MediaRecorder === 'undefined') return ''
  for (const mime of candidates) {
    if (MediaRecorder.isTypeSupported(mime)) return mime
  }
  return ''
}

function extensionForMime(mime) {
  if (!mime) return 'webm'
  if (mime.includes('webm')) return 'webm'
  if (mime.includes('ogg')) return 'ogg'
  if (mime.includes('mp4')) return 'm4a'
  if (mime.includes('wav')) return 'wav'
  return 'webm'
}

function stopMediaTracks() {
  if (mediaStream) {
    mediaStream.getTracks().forEach((t) => t.stop())
    mediaStream = null
  }
  if (elapsedTimer) {
    clearInterval(elapsedTimer)
    elapsedTimer = null
  }
}

function dismissError() {
  recordError.value = ''
}

async function startRecording({ collectionId, canEdit } = {}) {
  if (!navigator.mediaDevices || typeof MediaRecorder === 'undefined') {
    recordError.value = 'Recording is not supported in this browser.'
    return
  }
  if (!collectionId) {
    recordError.value = 'Select a collection before recording.'
    return
  }
  if (canEdit === false) {
    recordError.value = 'You do not have permission to add sources to this collection.'
    return
  }

  recordError.value = ''
  cancelled = false
  recordedChunks = []

  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true })
  } catch (err) {
    recordError.value = err?.message?.includes('Permission')
      ? 'Microphone permission denied.'
      : 'Could not access microphone.'
    return
  }

  const mime = pickRecordingMime()
  try {
    mediaRecorder = mime
      ? new MediaRecorder(mediaStream, { mimeType: mime })
      : new MediaRecorder(mediaStream)
  } catch (err) {
    recordError.value = 'Failed to start recorder.'
    stopMediaTracks()
    return
  }

  // Pin the collection id at start time so a mid-recording collection switch
  // doesn't route the transcript into the wrong household.
  const targetCollectionId = collectionId

  mediaRecorder.ondataavailable = (e) => {
    if (e.data && e.data.size > 0) recordedChunks.push(e.data)
  }
  mediaRecorder.onstop = async () => {
    stopMediaTracks()
    if (cancelled) {
      recordedChunks = []
      isRecording.value = false
      return
    }
    const blob = new Blob(recordedChunks, { type: mediaRecorder.mimeType || 'audio/webm' })
    recordedChunks = []
    isRecording.value = false
    await uploadRecording(blob, targetCollectionId)
  }

  elapsedSeconds.value = 0
  elapsedTimer = setInterval(() => { elapsedSeconds.value += 1 }, 1000)
  mediaRecorder.start()
  isRecording.value = true
}

function stopRecording() {
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.stop()
  }
}

function cancelRecording() {
  cancelled = true
  stopRecording()
}

function toggleRecording(opts) {
  if (isRecording.value) stopRecording()
  else startRecording(opts)
}

async function uploadRecording(blob, collectionId) {
  transcribing.value = true
  transcribeStatus.value = 'Uploading recording…'
  try {
    const ext = extensionForMime(blob.type)
    const now = new Date()
    const pad = (n) => String(n).padStart(2, '0')
    const stamp = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
    const filename = `meeting-${stamp}.${ext}`
    const file = new File([blob], filename, { type: blob.type })

    const form = new FormData()
    form.append('files', file)

    transcribeStatus.value = 'Transcribing with Whisper (may take a minute)…'
    await axios.post('/documents/upload', form, {
      params: { collection_id: collectionId },
      headers: { 'Content-Type': 'multipart/form-data' },
    })

    window.dispatchEvent(new CustomEvent('asymptote:transcript-saved', {
      detail: { collectionId },
    }))
  } catch (err) {
    recordError.value = friendlyAudioError(err) || 'Failed to transcribe recording.'
  } finally {
    transcribing.value = false
    transcribeStatus.value = ''
  }
}

// Map a backend "unsupported type" rejection into something an advisor can
// actually act on — names the formats Finn does accept. Used by both the
// recorder and the file-picker upload path.
function friendlyAudioError(errOrFilename) {
  let detail = ''
  let filename = ''

  if (typeof errOrFilename === 'string') {
    filename = errOrFilename
  } else if (errOrFilename) {
    detail = errOrFilename.response?.data?.detail || errOrFilename.message || ''
  }

  // Backend returns `File X has unsupported type. Supported: ...`
  const match = detail.match(/^File (.+?) has unsupported type/i)
  if (match) filename = match[1]

  if (!filename) return detail || ''

  const ext = filename.split('.').pop()?.toLowerCase() || ''
  const supported = 'MP3, WAV, M4A, WebM, OGG, FLAC, MP4'

  if (KNOWN_UNSUPPORTED_AUDIO_EXTENSIONS.includes(ext)) {
    return `Audio format .${ext} isn't supported. Save as MP3, WAV, or M4A and try again. (Supported: ${supported}.)`
  }
  if (detail) return detail
  return ''
}

// Cheap pre-flight check for the file picker path so we never even POST a
// file we know the backend will reject.
function preflightAudioExtensionError(filename) {
  if (!filename) return ''
  const ext = filename.split('.').pop()?.toLowerCase() || ''
  if (KNOWN_UNSUPPORTED_AUDIO_EXTENSIONS.includes(ext)) {
    return `${filename}: audio format .${ext} isn't supported. Save as MP3, WAV, or M4A. (Supported: MP3, WAV, M4A, WebM, OGG, FLAC, MP4.)`
  }
  return ''
}

export function useMeetingRecorder() {
  return {
    isRecording,
    transcribing,
    transcribeStatus,
    recordError,
    elapsedSeconds,
    formattedElapsed,
    startRecording,
    stopRecording,
    cancelRecording,
    toggleRecording,
    dismissError,
    friendlyAudioError,
    preflightAudioExtensionError,
  }
}
