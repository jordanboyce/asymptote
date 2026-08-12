<!--
  RedactionBoundaryPanel — the Boundary Report.

  The Trust Report made ingest visible. This makes the redaction boundary
  visible, and it answers a different question than the audit log does. The log
  says "34 entities were replaced." This says: Robert Henderson, one person,
  mentioned nine times in two retrieved passages, left this machine as Marcus
  Whitfield — and here is the boundary he crossed at.

  The left-hand column is the point. It shows the real value from the local
  record beside the stand-in that actually travelled, so a prospect watches the
  substitution rather than being told about it. It stays hidden until asked
  for: `reveal` is a query parameter the server honours explicitly, so the
  default response carries no client data at all.

  Two placements, one component:
    variant="inline"  — a section on the Overview tab, for the compliance read.
    variant="drawer"  — inside the chat PII drawer, scoped to the turn just run.
-->
<template>
  <section :class="variant === 'inline' ? 'rounded-lg border border-base-300 bg-base-100' : 'flex flex-col min-h-0'">
    <!-- Header -->
    <header
      class="flex items-start justify-between gap-3 flex-wrap px-4 py-3 shrink-0"
      :class="variant === 'inline' ? 'border-b border-base-300/60' : ''"
    >
      <div class="min-w-0">
        <h2 class="text-sm font-semibold tracking-tight flex items-center gap-2">
          <component
            :is="protecting ? ShieldCheck : ShieldAlert"
            :size="15"
            class="shrink-0"
            :class="protecting ? 'text-primary' : 'text-warning'"
            aria-hidden="true"
          />
          What left this machine
        </h2>
        <p
          v-if="report"
          class="mt-1 text-xs"
          :class="protecting ? 'text-base-content/65' : 'text-warning'"
        >
          {{ report.headline }}
        </p>
        <p v-else-if="loading" class="mt-1 text-xs text-base-content/55">
          Reading the local audit log…
        </p>
      </div>

      <div class="flex items-center gap-2 shrink-0">
        <span v-if="report && report.total_redactions" class="badge badge-sm badge-success">
          {{ report.distinct_values }} replaced
        </span>
        <button
          class="btn btn-xs btn-ghost gap-1"
          @click="load"
          :disabled="loading"
          title="Re-read the audit log"
          aria-label="Refresh the boundary report"
        >
          <RefreshCw :size="12" :class="{ 'animate-spin': loading }" />
        </button>
      </div>
    </header>

    <div
      class="px-4 py-3 flex flex-col gap-3"
      :class="variant === 'drawer' ? 'flex-1 overflow-y-auto min-h-0' : ''"
    >
      <!-- Redaction off / engine down — the one thing that must never be quiet -->
      <div
        v-if="report && !protecting"
        role="alert"
        class="rounded-lg border border-warning/40 bg-warning/10 px-3 py-2.5 text-xs leading-relaxed"
      >
        <p class="font-medium text-base-content/90 flex items-center gap-1.5">
          <AlertTriangle :size="13" aria-hidden="true" />
          {{ report.enabled ? 'The redaction engine did not start' : 'Redaction is switched off' }}
        </p>
        <p class="mt-1 text-base-content/70">
          {{ report.enabled
            ? 'Nothing below is being scrubbed. Prompts may carry client identifiers as written.'
            : 'Names, account numbers, and other identifiers reach the AI provider unchanged.' }}
        </p>
        <button class="btn btn-xs btn-warning mt-2" @click="$emit('open-settings')">
          Open Privacy settings
        </button>
      </div>

      <!-- Scope + reveal controls -->
      <div v-if="showScope || report" class="flex items-center justify-between gap-2 flex-wrap">
        <div v-if="showScope" class="join" role="group" aria-label="Time window">
          <button
            v-for="opt in scopeOptions"
            :key="opt.value"
            class="btn btn-xs join-item"
            :class="scope === opt.value ? 'btn-active' : 'btn-ghost'"
            @click="scope = opt.value"
          >
            {{ opt.label }}
          </button>
        </div>
        <button
          v-if="report && report.total_redactions"
          class="btn btn-xs btn-ghost gap-1.5 ml-auto"
          @click="revealed = !revealed"
          :aria-pressed="revealed"
        >
          <component :is="revealed ? EyeOff : Eye" :size="12" aria-hidden="true" />
          {{ revealed ? 'Hide the real values' : 'Show the real values' }}
        </button>
      </div>

      <div v-if="error" role="alert" class="alert alert-error text-xs py-2">
        <AlertTriangle :size="14" aria-hidden="true" />
        <span>{{ error }}</span>
      </div>

      <div v-else-if="loading && !report" class="flex flex-col gap-2">
        <div v-for="i in 2" :key="i" class="h-16 rounded-lg bg-base-200 animate-pulse"></div>
      </div>

      <!-- Nothing crossed yet -->
      <p
        v-else-if="report && !report.boundaries.length"
        class="text-xs text-base-content/55 py-2 leading-relaxed"
      >
        Nothing has needed redacting in this window. Ask a question about a
        client document and every identifier Finn replaces will be listed here,
        beside the stand-in that travelled in its place.
      </p>

      <!-- One block per boundary -->
      <article
        v-for="boundary in (report?.boundaries || [])"
        :key="boundary.key"
        class="rounded-lg border border-base-300 bg-base-100"
      >
        <div class="px-3 py-2.5 flex items-start justify-between gap-3">
          <div class="min-w-0">
            <p class="text-sm font-medium">{{ boundary.label }}</p>
            <p class="mt-0.5 text-xs text-base-content/60 leading-relaxed">
              {{ boundary.description }}
            </p>
            <p v-if="boundary.tools.length" class="mt-1 text-[11px] text-base-content/40 truncate">
              {{ boundary.tools.join(' · ') }}
            </p>
          </div>
          <span class="badge badge-sm badge-ghost shrink-0 tabular-nums">
            {{ boundary.distinct_count }}
          </span>
        </div>

        <div class="border-t border-base-300/60 overflow-x-auto">
          <table class="table table-xs">
            <thead>
              <tr>
                <th v-if="revealed" class="font-medium">In your record</th>
                <th class="font-medium">What the AI saw</th>
                <th class="font-medium">Type</th>
                <th class="font-medium text-right">Times</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(sub, i) in boundary.substitutions" :key="boundary.key + i">
                <td v-if="revealed" class="font-mono text-[11px] max-w-[12rem] truncate" :title="sub.original">
                  <span class="inline-flex items-center gap-1.5">
                    <Lock :size="10" class="text-base-content/35 shrink-0" aria-hidden="true" />
                    {{ sub.original }}
                  </span>
                </td>
                <td class="font-mono text-[11px] max-w-[12rem] truncate" :title="sub.replacement">
                  <span class="inline-flex items-center gap-1.5">
                    <ArrowRight v-if="revealed" :size="10" class="text-base-content/35 shrink-0" aria-hidden="true" />
                    {{ sub.replacement }}
                  </span>
                </td>
                <td class="text-base-content/60">{{ sub.entity_label }}</td>
                <td class="text-right tabular-nums text-base-content/60">{{ sub.occurrences }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </article>

      <!-- The boundary with nothing to log, said out loud -->
      <details v-if="report?.unlogged_controls?.length" class="rounded-lg border border-base-300/60 group">
        <summary class="px-3 py-2 text-xs text-base-content/55 hover:text-base-content/80 cursor-pointer hover:bg-base-200/60 transition-colors">
          One more thing Finn protects, with nothing to list
        </summary>
        <div class="px-3 pb-3 pt-1 space-y-2">
          <div v-for="c in report.unlogged_controls" :key="c.label">
            <p class="text-xs font-medium">{{ c.label }}</p>
            <p class="mt-0.5 text-xs text-base-content/60 leading-relaxed">{{ c.description }}</p>
          </div>
        </div>
      </details>

      <p v-if="report" class="text-xs text-base-content/40 leading-relaxed">
        <template v-if="revealed">
          The left-hand column is read from the audit log on this computer. It
          has never been transmitted — only the right-hand column has.
        </template>
        <template v-else>
          Read from the audit log on this computer. No AI provider was called to
          produce this.
        </template>
        <template v-if="report.redaction_style === 'consistent_pseudonym'">
          Each client keeps the same stand-in name across every question, so
          answers stay coherent without the real name travelling.
        </template>
      </p>
    </div>
  </section>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import axios from 'axios'
import {
  ShieldCheck, ShieldAlert, AlertTriangle, RefreshCw,
  Eye, EyeOff, ArrowRight, Lock,
} from 'lucide-vue-next'

const props = defineProps({
  collectionId: { type: String, default: '' },
  // ISO timestamps. When present they drive the scope switcher — the demo
  // wants "the turn I just ran", not "everything since I opened the tab".
  turnStartedAt: { type: String, default: '' },
  sessionStartedAt: { type: String, default: '' },
  variant: { type: String, default: 'inline' },
})

defineEmits(['open-settings'])

const loading = ref(false)
const error = ref('')
const report = ref(null)
const revealed = ref(false)

const scopeOptions = computed(() => {
  const opts = []
  if (props.turnStartedAt) opts.push({ value: 'turn', label: 'This answer' })
  if (props.sessionStartedAt) opts.push({ value: 'session', label: 'This session' })
  opts.push({ value: 'all', label: 'Everything' })
  return opts
})

const showScope = computed(() => scopeOptions.value.length > 1)

const scope = ref(props.turnStartedAt ? 'turn' : props.sessionStartedAt ? 'session' : 'all')

const sinceParam = computed(() => {
  if (scope.value === 'turn') return props.turnStartedAt || ''
  if (scope.value === 'session') return props.sessionStartedAt || ''
  return ''
})

// "Protecting" means both switched on and actually running. A disabled engine
// that reports zero redactions looks identical to a clean turn, so the two are
// never allowed to render the same way.
const protecting = computed(
  () => !report.value || (report.value.enabled && report.value.engine_available),
)

const load = async () => {
  if (!props.collectionId) {
    report.value = null
    return
  }
  loading.value = true
  error.value = ''
  try {
    const params = { collection_id: props.collectionId }
    if (sinceParam.value) params.since = sinceParam.value
    if (revealed.value) params.reveal = true
    const { data } = await axios.get('/api/redactions/boundary-report', { params })
    report.value = data
  } catch (e) {
    error.value = e?.response?.data?.detail || 'Could not read the redaction record right now.'
  } finally {
    loading.value = false
  }
}

// Revealing is a re-fetch, not a client-side unhide: the originals are simply
// not in the payload until the server is asked for them.
watch([() => props.collectionId, scope, revealed], load)

// A new turn while the panel is open should follow the turn, not strand the
// viewer on the previous one.
watch(() => props.turnStartedAt, () => {
  if (scope.value === 'turn') load()
})

onMounted(load)

defineExpose({ load })
</script>
