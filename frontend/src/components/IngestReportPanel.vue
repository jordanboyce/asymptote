<!--
  IngestReportPanel — the Trust Report.

  Finn already knew all of this: which header row was real, which vendor
  profile matched, which columns it could name, and which ones would produce a
  wrong total if anyone added them up. Until now none of it was visible. This
  panel is that knowledge, written for someone who has never heard the word
  "coercion".

  Two placements, one component:
    variant="inline"  — a section on the Overview tab, always available.
    variant="modal"   — shown once after an upload, so the answer to "did my
                        file land properly?" arrives before it's asked.

  Everything here is computed locally. No AI provider is called and no data
  leaves the process to produce it.
-->
<template>
  <section :class="variant === 'inline' ? 'rounded-lg border border-base-300 bg-base-100' : ''">
    <!-- Header + overall verdict -->
    <header
      class="flex items-start justify-between gap-3 flex-wrap px-4 py-3"
      :class="variant === 'inline' ? 'border-b border-base-300/60' : ''"
    >
      <div class="min-w-0">
        <h2 class="text-sm font-semibold tracking-tight flex items-center gap-2">
          <ShieldCheck :size="15" class="text-primary shrink-0" aria-hidden="true" />
          Data health
        </h2>
        <p v-if="summary" class="mt-1 text-xs" :class="gradeTextClass(summary.grade)">
          {{ summary.headline }}
        </p>
        <p v-else-if="loading" class="mt-1 text-xs text-base-content/55">Checking your files…</p>
      </div>

      <div v-if="summary && summary.sheet_count" class="flex items-center gap-2 shrink-0">
        <span class="badge badge-sm" :class="gradeBadgeClass(summary.grade)">
          {{ gradeLabel(summary.grade) }}
        </span>
        <button
          class="btn btn-xs btn-ghost gap-1"
          @click="load"
          :disabled="loading"
          title="Re-check"
          aria-label="Re-check data health"
        >
          <RefreshCw :size="12" :class="{ 'animate-spin': loading }" />
        </button>
      </div>
    </header>

    <div class="px-4 py-3 flex flex-col gap-3">
      <!-- Error -->
      <div v-if="error" role="alert" class="alert alert-error text-xs py-2">
        <AlertTriangle :size="14" aria-hidden="true" />
        <span>{{ error }}</span>
      </div>

      <!-- Loading -->
      <div v-else-if="loading && !reports.length" class="flex flex-col gap-2">
        <div v-for="i in 2" :key="i" class="h-16 rounded-lg bg-base-200 animate-pulse"></div>
      </div>

      <!-- Nothing ingested yet -->
      <p v-else-if="!reports.length" class="text-xs text-base-content/55 py-2">
        No spreadsheets or statements have been loaded yet. Upload a brokerage
        export and Finn will show you exactly what it made of it.
      </p>

      <!-- One block per ingested sheet -->
      <article
        v-for="report in reports"
        :key="report.table_name"
        class="rounded-lg border bg-base-100"
        :class="report.grade === 'unreliable' ? 'border-error/40' : 'border-base-300'"
      >
        <!-- File line -->
        <div class="px-3 py-2.5 flex items-start justify-between gap-3 flex-wrap">
          <div class="min-w-0">
            <p class="text-sm font-medium truncate flex items-center gap-1.5">
              <FileSpreadsheet :size="14" class="text-base-content/45 shrink-0" aria-hidden="true" />
              <span class="truncate">{{ report.filename }}</span>
              <span v-if="report.sheet_name" class="text-xs font-normal text-base-content/50">
                · {{ report.sheet_name }}
              </span>
            </p>
            <p class="mt-1 text-xs text-base-content/60 tabular-nums">
              {{ report.row_count.toLocaleString() }} rows
              <span class="mx-1 text-base-content/25">·</span>
              {{ report.columns_understood }} of {{ report.column_count }} columns understood
              <template v-if="report.recognized_as">
                <span class="mx-1 text-base-content/25">·</span>
                {{ report.recognized_as }}
              </template>
              <template v-if="report.read_as">
                <span class="mx-1 text-base-content/25">·</span>
                <span class="text-base-content/50">read as {{ report.read_as }}</span>
              </template>
            </p>
          </div>
          <span class="badge badge-sm shrink-0" :class="gradeBadgeClass(report.grade)">
            {{ gradeLabel(report.grade) }}
          </span>
        </div>

        <!-- Findings -->
        <ul v-if="report.findings.length" class="border-t border-base-300/60 divide-y divide-base-300/40">
          <li
            v-for="(finding, i) in visibleFindings(report)"
            :key="finding.code + i"
            class="px-3 py-2.5 flex items-start gap-2.5"
          >
            <component
              :is="severityIcon(finding.severity)"
              :size="14"
              class="mt-0.5 shrink-0"
              :class="severityTextClass(finding.severity)"
              aria-hidden="true"
            />
            <div class="min-w-0 flex-1">
              <p class="text-xs font-medium" :class="severityTextClass(finding.severity)">
                {{ finding.headline }}
              </p>
              <p class="mt-0.5 text-xs text-base-content/65 leading-relaxed">
                {{ finding.detail }}
              </p>
              <p v-if="finding.suggestion" class="mt-1 text-xs text-base-content/50 italic">
                {{ finding.suggestion }}
              </p>
              <button
                v-if="finding.column"
                class="mt-1.5 btn btn-xs btn-ghost gap-1 -ml-2"
                @click="askAbout(report, finding)"
              >
                <MessageSquare :size="11" />
                Ask about this column
              </button>
            </div>
          </li>
        </ul>

        <!-- Routine notes fold away — only what needs attention shows by default -->
        <button
          v-if="hiddenCount(report)"
          class="w-full px-3 py-1.5 text-xs text-base-content/50 hover:text-base-content/80 hover:bg-base-200/60 border-t border-base-300/60 text-left transition-colors"
          @click="toggleExpanded(report.table_name)"
        >
          {{ expanded[report.table_name]
            ? 'Hide routine notes'
            : `Show ${hiddenCount(report)} routine note${hiddenCount(report) === 1 ? '' : 's'}` }}
        </button>

        <!-- The full column map, for the advisor who wants to verify rather than trust -->
        <details class="border-t border-base-300/60 group">
          <summary class="px-3 py-1.5 text-xs text-base-content/50 hover:text-base-content/80 cursor-pointer hover:bg-base-200/60 transition-colors">
            Show every column Finn read
          </summary>
          <div class="px-3 pb-3 pt-1 overflow-x-auto">
            <table class="table table-xs">
              <thead>
                <tr>
                  <th class="font-medium">Column in your file</th>
                  <th class="font-medium">Finn read it as</th>
                  <th class="font-medium">How it decided</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="col in report.understood" :key="'u' + col.name">
                  <td class="font-mono text-[11px]">{{ col.name }}</td>
                  <td>
                    {{ col.role_label }}
                    <span
                      v-if="col.role_band && col.role_band !== 'confirmed'"
                      class="badge badge-xs ml-1 align-middle"
                      :class="col.role_band === 'uncertain' ? 'badge-warning' : 'badge-ghost'"
                      :title="(col.role_signals || []).join(' · ')"
                    >{{ col.role_band }}</span>
                  </td>
                  <td class="text-base-content/55">{{ col.role_source_label || '—' }}</td>
                </tr>
                <tr v-for="col in report.unnamed" :key="'n' + col.name" class="text-base-content/50">
                  <td class="font-mono text-[11px]">{{ col.name }}</td>
                  <td class="italic">Kept, but not named</td>
                  <td>—</td>
                </tr>
              </tbody>
            </table>
          </div>
        </details>
      </article>

      <p v-if="reports.length" class="text-xs text-base-content/40 leading-relaxed">
        Checked on this machine. No AI provider was called and nothing left your
        computer to produce this.
      </p>
    </div>
  </section>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import axios from 'axios'
import {
  ShieldCheck, AlertTriangle, AlertCircle, Info, RefreshCw,
  FileSpreadsheet, MessageSquare,
} from 'lucide-vue-next'

const props = defineProps({
  collectionId: { type: String, default: '' },
  documentId: { type: String, default: '' },
  variant: { type: String, default: 'inline' },
})

const emit = defineEmits(['send-to-chat', 'loaded'])

const loading = ref(false)
const error = ref('')
const reports = ref([])
const summary = ref(null)
const expanded = ref({})

// `info` findings are things Finn did correctly and routinely — skipped a
// preamble, matched a vendor profile. They're the proof the parse was real,
// but they shouldn't compete with a blocker for attention, so they collapse
// unless the report is otherwise clean (in which case they're all there is).
const NOISE_SEVERITIES = ['info']

const visibleFindings = (report) => {
  if (expanded.value[report.table_name]) return report.findings
  const notable = report.findings.filter(f => !NOISE_SEVERITIES.includes(f.severity))
  return notable.length ? notable : report.findings
}

const hiddenCount = (report) => {
  const total = report.findings.length
  const shown = visibleFindings(report).length
  return expanded.value[report.table_name] ? 0 : total - shown
}

const toggleExpanded = (tableName) => {
  expanded.value = { ...expanded.value, [tableName]: !expanded.value[tableName] }
}

const gradeLabel = (grade) => ({
  clean: 'Clean',
  needs_review: 'Worth a look',
  unreliable: 'Needs a fix',
}[grade] || grade)

const gradeBadgeClass = (grade) => ({
  clean: 'badge-success',
  needs_review: 'badge-warning',
  unreliable: 'badge-error',
}[grade] || 'badge-ghost')

const gradeTextClass = (grade) => ({
  clean: 'text-success',
  needs_review: 'text-warning',
  unreliable: 'text-error',
}[grade] || 'text-base-content/60')

const severityIcon = (severity) => ({
  blocker: AlertCircle,
  warning: AlertTriangle,
  info: Info,
}[severity] || Info)

const severityTextClass = (severity) => ({
  blocker: 'text-error',
  warning: 'text-warning',
  info: 'text-base-content/55',
}[severity] || 'text-base-content/55')

const askAbout = (report, finding) => {
  emit(
    'send-to-chat',
    `In ${report.filename}, what does the "${finding.column}" column contain? ` +
    `Show me the rows where it isn't a plain number.`
  )
}

const load = async () => {
  if (!props.collectionId) {
    reports.value = []
    summary.value = null
    return
  }
  loading.value = true
  error.value = ''
  try {
    const { data } = await axios.get(
      `/api/collections/${props.collectionId}/ingest-report`,
      { params: props.documentId ? { document_id: props.documentId } : {} }
    )
    reports.value = data.reports || []
    summary.value = data.summary || null
    emit('loaded', data)
  } catch (e) {
    error.value = e?.response?.data?.detail || 'Could not check your files right now.'
  } finally {
    loading.value = false
  }
}

watch(() => [props.collectionId, props.documentId], load)
onMounted(load)

defineExpose({ load })
</script>
