<template>
  <div class="space-y-4">
    <!-- Header -->
    <div class="flex items-end justify-between flex-wrap gap-3 pb-3 border-b border-base-300/60">
      <div>
        <h1 class="text-[20px] leading-none font-semibold tracking-tight">Diagnostics</h1>
        <p class="mt-2 text-xs text-base-content/55">
          In-memory log + chat-event capture for development. Restart clears the buffers.
          <span class="text-base-content/40">·</span>
          Auto-refresh every 5s while this tab is open.
        </p>
      </div>
      <div class="flex items-center gap-2">
        <button class="btn btn-ghost btn-sm" @click="refresh" :disabled="loading">
          <span v-if="loading" class="loading loading-spinner loading-xs"></span>
          Refresh
        </button>
        <button class="btn btn-ghost btn-sm text-error" @click="clearBuffers" :disabled="loading">
          Clear
        </button>
      </div>
    </div>

    <!-- View toggle + filter -->
    <div class="flex items-center gap-3 flex-wrap">
      <div role="tablist" class="tabs tabs-boxed bg-base-200">
        <button
          role="tab"
          class="tab text-sm"
          :class="view === 'chat' ? 'tab-active' : ''"
          @click="view = 'chat'"
        >
          Chat events
          <span class="ml-1.5 badge badge-xs">{{ filteredEvents.length }}</span>
        </button>
        <button
          role="tab"
          class="tab text-sm"
          :class="view === 'logs' ? 'tab-active' : ''"
          @click="view = 'logs'"
        >
          Logs
          <span class="ml-1.5 badge badge-xs">{{ filteredLogs.length }}</span>
        </button>
      </div>

      <input
        v-model="filter"
        type="text"
        placeholder="Filter (substring)…"
        class="input input-bordered input-sm flex-1 min-w-[200px] max-w-md"
      />

      <select
        v-if="view === 'logs'"
        v-model="logLevel"
        class="select select-bordered select-sm"
        @change="refresh"
      >
        <option value="DEBUG">DEBUG+</option>
        <option value="INFO">INFO+</option>
        <option value="WARNING">WARNING+</option>
        <option value="ERROR">ERROR+</option>
      </select>

      <div class="flex items-center gap-2 ml-auto">
        <button class="btn btn-sm btn-primary" @click="copyAsMarkdown">
          Copy as Markdown
        </button>
        <button class="btn btn-sm btn-ghost" @click="copyAsJson">
          Copy as JSON
        </button>
      </div>
    </div>
    <p v-if="copyToast" class="text-xs text-success">{{ copyToast }}</p>

    <!-- Chat events view -->
    <div v-if="view === 'chat'" class="space-y-2">
      <div
        v-if="filteredEvents.length === 0"
        class="text-sm text-base-content/55 italic px-4 py-8 text-center border border-dashed border-base-300 rounded-lg"
      >
        No chat events yet. Send a message in Chat and refresh.
      </div>
      <div
        v-for="(group, idx) in groupedEvents"
        :key="group.turnId + idx"
        class="rounded-lg border border-base-300 bg-base-100 overflow-hidden"
      >
        <div class="flex items-center justify-between gap-3 px-3 py-2 bg-base-200/60 text-xs">
          <div class="flex items-center gap-2 min-w-0">
            <span class="font-mono text-base-content/55">turn {{ group.turnId }}</span>
            <span class="text-base-content/35">·</span>
            <span class="text-base-content/55">{{ group.collectionId }}</span>
            <span class="text-base-content/35">·</span>
            <span class="text-base-content/55">{{ formatRelative(group.startedAt) }}</span>
            <span class="text-base-content/35">·</span>
            <span class="text-base-content/55">{{ group.events.length }} events</span>
          </div>
          <span
            v-if="group.errorCount > 0"
            class="badge badge-error badge-sm"
          >{{ group.errorCount }} error{{ group.errorCount > 1 ? 's' : '' }}</span>
        </div>
        <div class="divide-y divide-base-300/60">
          <div
            v-for="(ev, i) in group.events"
            :key="i"
            class="px-3 py-2 text-xs font-mono"
            :class="ev.type === 'error' ? 'bg-error/5' : ''"
          >
            <div class="flex items-baseline gap-2">
              <span class="text-base-content/40 tabular-nums">{{ formatTime(ev.ts) }}</span>
              <span
                class="font-semibold"
                :class="eventTypeClass(ev.type)"
              >{{ ev.type }}</span>
              <span v-if="ev.payload?.tool" class="text-base-content/70">{{ ev.payload.tool }}</span>
            </div>
            <pre class="mt-1 whitespace-pre-wrap break-all text-base-content/75">{{ formatPayload(ev.payload) }}</pre>
          </div>
        </div>
      </div>
    </div>

    <!-- Logs view -->
    <div v-else class="space-y-1">
      <div
        v-if="filteredLogs.length === 0"
        class="text-sm text-base-content/55 italic px-4 py-8 text-center border border-dashed border-base-300 rounded-lg"
      >
        No log records at this level.
      </div>
      <div
        v-for="(rec, i) in filteredLogs"
        :key="i"
        class="px-3 py-1.5 text-xs font-mono rounded border border-base-300/40"
        :class="logLevelClass(rec.level)"
      >
        <div class="flex items-baseline gap-2 flex-wrap">
          <span class="text-base-content/40 tabular-nums">{{ formatTime(rec.ts) }}</span>
          <span class="font-semibold" :class="logLevelTextClass(rec.level)">{{ rec.level }}</span>
          <span class="text-base-content/55">{{ rec.logger }}</span>
        </div>
        <div class="mt-0.5 whitespace-pre-wrap break-all">{{ rec.message }}</div>
        <pre v-if="rec.exc_info" class="mt-1 text-error/80 whitespace-pre-wrap text-[11px]">{{ rec.exc_info }}</pre>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import axios from 'axios'

const view = ref('chat')
const filter = ref('')
const logLevel = ref('INFO')
const logs = ref([])
const events = ref([])
const loading = ref(false)
const copyToast = ref('')

let copyToastTimer = null
let pollTimer = null

const showCopyToast = (msg) => {
  copyToast.value = msg
  if (copyToastTimer) clearTimeout(copyToastTimer)
  copyToastTimer = setTimeout(() => { copyToast.value = '' }, 2000)
}

async function refresh() {
  loading.value = true
  try {
    const [logsResp, eventsResp] = await Promise.all([
      axios.get('/api/diagnostics/logs', { params: { limit: 500, level: logLevel.value } }),
      axios.get('/api/diagnostics/chat-events', { params: { limit: 200 } }),
    ])
    logs.value = logsResp.data?.logs || []
    events.value = eventsResp.data?.events || []
  } catch (err) {
    console.error('Diagnostics fetch failed:', err)
  } finally {
    loading.value = false
  }
}

async function clearBuffers() {
  if (!confirm('Clear all in-memory diagnostics? This cannot be undone.')) return
  try {
    await axios.post('/api/diagnostics/clear')
    logs.value = []
    events.value = []
    showCopyToast('Buffers cleared.')
  } catch (err) {
    console.error('Clear failed:', err)
  }
}

const lowerFilter = computed(() => filter.value.trim().toLowerCase())

const filteredLogs = computed(() => {
  const q = lowerFilter.value
  if (!q) return logs.value
  return logs.value.filter(r =>
    (r.message || '').toLowerCase().includes(q) ||
    (r.logger || '').toLowerCase().includes(q) ||
    (r.exc_info || '').toLowerCase().includes(q)
  )
})

const filteredEvents = computed(() => {
  const q = lowerFilter.value
  if (!q) return events.value
  return events.value.filter(e => {
    if ((e.type || '').toLowerCase().includes(q)) return true
    if ((e.payload?.tool || '').toLowerCase().includes(q)) return true
    try { return JSON.stringify(e.payload || {}).toLowerCase().includes(q) } catch { return false }
  })
})

const groupedEvents = computed(() => {
  const groups = new Map()
  for (const ev of filteredEvents.value) {
    const key = ev.turn_id || 'unknown'
    if (!groups.has(key)) {
      groups.set(key, {
        turnId: key,
        collectionId: ev.collection_id || '',
        startedAt: ev.ts,
        events: [],
        errorCount: 0,
      })
    }
    const g = groups.get(key)
    g.events.push(ev)
    if (ev.type === 'error') g.errorCount += 1
    if (ev.ts < g.startedAt) g.startedAt = ev.ts
  }
  // Newest turn first
  return Array.from(groups.values()).sort((a, b) => b.startedAt - a.startedAt)
})

function formatTime(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString(undefined, { hour12: false }) + '.' + String(d.getMilliseconds()).padStart(3, '0')
}

function formatRelative(ts) {
  if (!ts) return ''
  const delta = Date.now() / 1000 - ts
  if (delta < 60) return `${Math.round(delta)}s ago`
  if (delta < 3600) return `${Math.round(delta / 60)}m ago`
  return `${Math.round(delta / 3600)}h ago`
}

function formatPayload(p) {
  if (!p || (typeof p === 'object' && !Object.keys(p).length)) return ''
  try { return JSON.stringify(p, null, 2) } catch { return String(p) }
}

function eventTypeClass(t) {
  if (t === 'error') return 'text-error'
  if (t === 'tool_start') return 'text-primary'
  if (t === 'tool_end') return 'text-success'
  if (t === 'thinking') return 'text-base-content/70'
  if (t === 'done' || t === 'turn_start') return 'text-info'
  return 'text-base-content/70'
}

function logLevelClass(level) {
  switch (level) {
    case 'ERROR':
    case 'CRITICAL':
      return 'bg-error/5 border-error/20'
    case 'WARNING':
      return 'bg-warning/5 border-warning/20'
    default:
      return 'bg-base-100'
  }
}

function logLevelTextClass(level) {
  switch (level) {
    case 'ERROR':
    case 'CRITICAL':
      return 'text-error'
    case 'WARNING':
      return 'text-warning'
    case 'INFO':
      return 'text-info'
    default:
      return 'text-base-content/55'
  }
}

// ── Copy helpers ─────────────────────────────────────────────────────────────

function copyAsJson() {
  const data = view.value === 'chat'
    ? { chat_events: filteredEvents.value }
    : { logs: filteredLogs.value }
  navigator.clipboard.writeText(JSON.stringify(data, null, 2))
    .then(() => showCopyToast('Copied JSON to clipboard.'))
    .catch(() => showCopyToast('Copy failed.'))
}

function copyAsMarkdown() {
  let md
  if (view.value === 'chat') {
    md = renderChatEventsMarkdown(filteredEvents.value)
  } else {
    md = renderLogsMarkdown(filteredLogs.value)
  }
  navigator.clipboard.writeText(md)
    .then(() => showCopyToast('Copied Markdown to clipboard.'))
    .catch(() => showCopyToast('Copy failed.'))
}

function renderChatEventsMarkdown(evs) {
  if (!evs.length) return '_No chat events captured._'
  const groups = new Map()
  for (const ev of evs) {
    const key = ev.turn_id || 'unknown'
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key).push(ev)
  }
  const lines = ['# Chat events', '']
  for (const [turnId, items] of groups) {
    const collectionId = items[0]?.collection_id || ''
    lines.push(`## Turn \`${turnId}\` (collection \`${collectionId}\`)`)
    lines.push('')
    for (const ev of items) {
      lines.push(`- **${formatTime(ev.ts)} · ${ev.type}**${ev.payload?.tool ? ' · ' + ev.payload.tool : ''}`)
      const payload = formatPayload(ev.payload)
      if (payload) {
        lines.push('  ```json')
        for (const line of payload.split('\n')) lines.push('  ' + line)
        lines.push('  ```')
      }
    }
    lines.push('')
  }
  return lines.join('\n')
}

function renderLogsMarkdown(records) {
  if (!records.length) return '_No log records captured at this level._'
  const lines = [`# Logs (${logLevel.value}+)`, '']
  for (const r of records) {
    lines.push(`- **${formatTime(r.ts)} · ${r.level} · ${r.logger}** — ${r.message}`)
    if (r.exc_info) {
      lines.push('  ```')
      for (const line of r.exc_info.split('\n')) lines.push('  ' + line)
      lines.push('  ```')
    }
  }
  return lines.join('\n')
}

onMounted(() => {
  refresh()
  pollTimer = setInterval(refresh, 5000)
})

onBeforeUnmount(() => {
  if (pollTimer) clearInterval(pollTimer)
  if (copyToastTimer) clearTimeout(copyToastTimer)
})
</script>
