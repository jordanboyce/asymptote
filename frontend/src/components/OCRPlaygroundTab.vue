<template>
  <div class="space-y-6">

    <!-- OCR Preview -->
    <div class="card bg-base-200">
      <div class="card-body">
        <h3 class="card-title">OCR Preview</h3>
        <p class="text-sm text-base-content/70 mb-2">
          Test extraction on a local PDF using the OCR settings configured in Settings. This preview does not store data.
        </p>

        <div v-if="!ocrEnabled" class="alert alert-warning py-2 mb-3">
          <span>OCR is disabled. Enable it in <button class="link link-primary" @click="$emit('switch-tab', 'settings')">Settings</button> to test Vision AI or Docling extraction.</span>
        </div>

        <div class="flex flex-wrap gap-2 items-center">
          <button class="btn btn-sm btn-outline" @click="pickOCRFile" :disabled="ocrPlaygroundRunning">
            Choose PDF...
          </button>
          <label for="ocr-file-path" class="sr-only">PDF file path</label>
          <input
            id="ocr-file-path"
            v-model="ocrPlaygroundPath"
            class="input input-bordered input-sm flex-1 min-w-[320px]"
            placeholder="C:\path\to\scan.pdf"
          />
          <button
            class="btn btn-sm btn-primary"
            @click="runOCRPlayground"
            :disabled="ocrPlaygroundRunning || !ocrPlaygroundPath.trim() || !ocrEnabled"
          >
            <span v-if="ocrPlaygroundRunning" class="loading loading-spinner loading-xs"></span>
            {{ ocrPlaygroundRunning ? 'Running...' : 'Run Preview' }}
          </button>
        </div>

        <div class="mt-3 rounded-xl border border-warning/40 bg-warning/10 p-3">
          <label class="label cursor-pointer justify-start gap-4 p-0">
            <input type="checkbox" class="toggle toggle-warning toggle-sm" v-model="forceOcr" />
            <div>
              <span class="label-text font-medium">Force Vision OCR</span>
              <p class="text-xs text-base-content/60">
                Skip native PDF text extraction entirely and always use the vision model.
                Use this when the PDF has a corrupt or garbled embedded text layer.
              </p>
            </div>
          </label>
        </div>

        <div class="mt-3 rounded-xl border border-base-300 bg-base-100 p-3">
          <label class="label cursor-pointer justify-start gap-4 p-0">
            <input type="checkbox" class="toggle toggle-primary toggle-sm" v-model="normalizePreviewText" />
            <div>
              <span class="label-text font-medium">Normalize OCR Text in Preview</span>
              <p class="text-xs text-base-content/60">Preview-only cleanup for noise and separator lines. Does not affect indexing.</p>
            </div>
          </label>
        </div>

        <div v-if="ocrPlaygroundError" class="alert alert-error mt-3 py-2">
          <span>{{ ocrPlaygroundError }}</span>
        </div>

        <div v-if="ocrPlaygroundResult" class="mt-4 space-y-3">
          <div class="flex flex-wrap gap-2">
            <span class="badge badge-outline">{{ ocrPlaygroundResult.filename }}</span>
            <span class="badge badge-info">{{ ocrPlaygroundResult.extraction_method || 'text' }}</span>
            <span class="badge badge-outline">{{ ocrPlaygroundResult.total_pages }} pages</span>
            <span class="badge badge-outline">{{ ocrPlaygroundResult.total_chars }} chars</span>
            <span class="badge badge-outline" :title="'Average characters per page extracted by native PDF reader. OCR triggers below 50.'">
              {{ ocrPlaygroundResult.avg_chars_per_page }} avg chars/page
            </span>
            <span v-if="ocrPlaygroundResult.force_ocr" class="badge badge-warning">force OCR</span>
            <span
              v-if="ocrPlaygroundResult.cleanup_pages && ocrPlaygroundResult.cleanup_pages.length"
              class="badge badge-success"
              :title="'LLM cleanup applied to pages: ' + ocrPlaygroundResult.cleanup_pages.join(', ')"
            >
              LLM cleanup: {{ ocrPlaygroundResult.cleanup_pages.length }}/{{ ocrPlaygroundResult.total_pages }} pages
            </span>
            <span v-else-if="ocrPlaygroundResult.extraction_method === 'ocr' || ocrPlaygroundResult.extraction_method === 'hybrid'" class="badge badge-warning">no LLM cleanup</span>
            <span v-if="ocrPlaygroundResult.normalize_preview_text" class="badge badge-secondary">preview cleanup on</span>
          </div>

          <!-- Injection Scan Summary -->
          <div v-if="ocrPlaygroundResult.injection_scan" class="rounded-xl border p-3"
               :class="injectionScanClass(ocrPlaygroundResult.injection_scan)">
            <div class="flex items-center gap-2 mb-1">
              <span class="font-semibold text-sm">Injection Scan</span>
              <span class="badge badge-sm"
                    :class="injectionBadgeClass(ocrPlaygroundResult.injection_scan)">
                {{ injectionRiskLabel(ocrPlaygroundResult.injection_scan) }}
              </span>
              <span class="text-xs opacity-70">
                score: {{ ocrPlaygroundResult.injection_scan.overall_risk_score.toFixed(2) }}
              </span>
            </div>
            <div v-if="ocrPlaygroundResult.injection_scan.overall_flagged" class="text-xs opacity-80">
              Flagged pages: {{ ocrPlaygroundResult.injection_scan.flagged_pages.join(', ') }}
            </div>
            <div v-else class="text-xs opacity-70">No injection signals detected in any page.</div>
          </div>

          <div class="space-y-2 max-h-96 overflow-y-auto pr-1">
            <details
              v-for="page in ocrPlaygroundResult.pages"
              :key="`ocr_page_${page.page_number}`"
              class="collapse collapse-arrow bg-base-100 border"
              :class="page.injection_scan?.is_flagged ? 'border-warning' : 'border-base-300'"
            >
              <summary class="collapse-title text-sm font-medium">
                Page {{ page.page_number }} | {{ page.char_count }} chars
                <span v-if="page.injection_scan?.is_flagged" class="badge badge-warning badge-sm ml-2">
                  injection risk
                </span>
              </summary>
              <div class="collapse-content">
                <!-- Per-page injection findings -->
                <div v-if="page.injection_scan?.is_flagged" class="mt-2 rounded-lg bg-warning/10 border border-warning/30 p-2 space-y-1">
                  <p class="text-xs font-semibold text-warning-content/80">
                    Injection signals detected (score: {{ page.injection_scan.risk_score.toFixed(2) }})
                  </p>
                  <div
                    v-for="(finding, fi) in page.injection_scan.findings"
                    :key="fi"
                    class="text-xs rounded bg-base-100/60 p-2 border border-base-300"
                  >
                    <div class="flex items-center gap-2 mb-0.5">
                      <span class="badge badge-xs"
                            :class="{ 'badge-error': finding.severity === 'high', 'badge-warning': finding.severity === 'medium', 'badge-ghost': finding.severity === 'low' }">
                        {{ finding.severity }}
                      </span>
                      <span class="font-mono font-medium">{{ finding.pattern_name }}</span>
                      <span class="opacity-50">· {{ finding.category }}</span>
                    </div>
                    <p class="font-mono text-[10px] opacity-75 break-all">{{ finding.matched_text }}</p>
                  </div>
                </div>
                <pre class="text-xs whitespace-pre-wrap bg-base-200 p-3 rounded border border-base-300 mt-2">{{ page.text_preview }}</pre>
              </div>
            </details>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import axios from 'axios'

defineEmits(['switch-tab'])

// OCR config loaded from backend (read-only here, configured in Settings)
const ocrEnabled = ref(false)
const ocrConfig = ref({})

const ocrPlaygroundPath = ref('')
const forceOcr = ref(false)
const normalizePreviewText = ref(false)
const ocrPlaygroundRunning = ref(false)
const ocrPlaygroundError = ref('')
const ocrPlaygroundResult = ref(null)

const loadOCRConfig = async () => {
  try {
    const response = await axios.get('/api/config')
    ocrEnabled.value = response.data.enable_ocr || false
    ocrConfig.value = response.data
  } catch {
    // Use defaults
  }
}

const pickOCRFile = async () => {
  ocrPlaygroundError.value = ''
  try {
    const response = await axios.post('/api/file-picker', null, { params: { multiple: false } })
    if (response.data.paths?.length > 0) {
      ocrPlaygroundPath.value = response.data.paths[0]
    }
  } catch (error) {
    ocrPlaygroundError.value = error.response?.data?.detail || 'Failed to open file picker'
  }
}

const runOCRPlayground = async () => {
  if (!ocrPlaygroundPath.value.trim()) return

  ocrPlaygroundRunning.value = true
  ocrPlaygroundError.value = ''
  ocrPlaygroundResult.value = null

  try {
    // Reload config to pick up any changes made in Settings
    await loadOCRConfig()
    const cfg = ocrConfig.value

    const response = await axios.post('/api/ocr/playground', {
      file_path: ocrPlaygroundPath.value.trim(),
      enable_ocr: cfg.enable_ocr || false,
      ocr_max_pages: cfg.ocr_max_pages ?? 25,
      ocr_max_file_mb: cfg.ocr_max_file_mb ?? 50,
      vision_ocr_provider: cfg.vision_ocr_provider || 'none',
      vision_ocr_model: cfg.vision_ocr_model || '',
      vision_ocr_api_key: cfg.vision_ocr_api_key || '',
      vision_ocr_dpi: cfg.vision_ocr_dpi ?? 150,
      vision_ocr_enhance_image: cfg.vision_ocr_enhance_image ?? true,
      vision_ocr_cleanup_pass: cfg.vision_ocr_cleanup_pass ?? true,
      vision_ocr_cleanup_model: cfg.vision_ocr_cleanup_model || '',
      vision_ocr_ollama_url: cfg.vision_ocr_ollama_url || 'http://localhost:11434',
      vision_ocr_form_mode: cfg.vision_ocr_form_mode ?? false,
      force_ocr: forceOcr.value,
      normalize_preview_text: normalizePreviewText.value,
      include_fields: false,
      max_chars_per_page: 6000,
    })
    ocrPlaygroundResult.value = response.data
  } catch (error) {
    ocrPlaygroundError.value = error.response?.data?.detail || 'OCR preview failed'
  } finally {
    ocrPlaygroundRunning.value = false
  }
}

const injectionRiskLabel = (scan) => {
  if (!scan.overall_flagged) return 'Clean'
  if (scan.overall_risk_score >= 0.6) return 'High Risk'
  if (scan.overall_risk_score >= 0.3) return 'Suspicious'
  return 'Low Risk'
}

const injectionBadgeClass = (scan) => {
  if (!scan.overall_flagged) return 'badge-success'
  if (scan.overall_risk_score >= 0.6) return 'badge-error'
  return 'badge-warning'
}

const injectionScanClass = (scan) => {
  if (!scan.overall_flagged) return 'border-success/30 bg-success/5'
  if (scan.overall_risk_score >= 0.6) return 'border-error/40 bg-error/5'
  return 'border-warning/40 bg-warning/5'
}

onMounted(() => {
  loadOCRConfig()
})
</script>
