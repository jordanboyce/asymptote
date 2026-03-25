<template>
  <div class="space-y-6">
    <h2 class="text-2xl font-bold">OCR</h2>

    <!-- OCR Settings -->
    <div class="card bg-base-200">
      <div class="card-body">
        <h3 class="card-title">OCR Settings</h3>
        <p class="text-sm text-base-content/70 mb-4">
          Configure OCR for scanned PDFs. These settings apply to both document indexing and the preview below.
        </p>

        <div class="form-control mb-4">
          <label class="label cursor-pointer justify-start gap-4">
            <input
              type="checkbox"
              class="toggle toggle-primary toggle-sm"
              v-model="ocrEnabled"
              @change="saveOCRSettings"
            />
            <div>
              <span class="label-text font-medium">Enable OCR for Scanned PDFs</span>
              <p class="text-xs text-base-content/60">
                When enabled, scanned PDFs with little or no native text will be processed with OCR during indexing.
              </p>
            </div>
          </label>
        </div>

        <div v-if="ocrEnabled" class="space-y-6">
          <!-- Vision AI Provider -->
          <section class="rounded-xl border border-base-300 bg-base-100 p-4 shadow-sm">
            <div class="mb-4">
              <h4 class="text-sm font-semibold uppercase tracking-[0.18em] text-base-content/70">Vision AI Provider</h4>
              <p class="mt-1 text-xs text-base-content/60">
                Vision AI sends each PDF page as an image to a language model for text extraction.
                More accurate than traditional OCR for complex layouts and degraded scans.
                Set provider to "None" to use Docling (free, local) as fallback when available.
              </p>
            </div>

            <div class="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              <div class="form-control">
                <label class="label pb-1">
                  <span class="label-text font-medium">Provider</span>
                </label>
                <select v-model="visionProvider" class="select select-bordered w-full" @change="onProviderChange">
                  <option value="none">None (Docling fallback)</option>
                  <option value="openai">OpenAI</option>
                  <option value="anthropic">Anthropic</option>
                  <option value="inl_hpc">INL HPC</option>
                  <option value="ollama">Ollama (local)</option>
                </select>
              </div>

              <!-- OpenAI model -->
              <div class="form-control" v-if="visionProvider === 'openai'">
                <label class="label pb-1"><span class="label-text font-medium">Model</span></label>
                <select v-model="visionModel" class="select select-bordered w-full" @change="saveOCRSettings">
                  <option value="gpt-4o">gpt-4o (best quality)</option>
                  <option value="gpt-4o-mini">gpt-4o-mini (faster, cheaper)</option>
                </select>
              </div>

              <!-- Anthropic model -->
              <div class="form-control" v-else-if="visionProvider === 'anthropic'">
                <label class="label pb-1"><span class="label-text font-medium">Model</span></label>
                <select v-model="visionModel" class="select select-bordered w-full" @change="saveOCRSettings">
                  <option value="claude-opus-4-6">claude-opus-4-6 (best quality)</option>
                  <option value="claude-sonnet-4-5-20250929">claude-sonnet-4-5 (balanced)</option>
                  <option value="claude-haiku-4-5-20251001">claude-haiku-4-5 (fastest)</option>
                </select>
              </div>

              <!-- INL HPC model -->
              <div class="form-control" v-else-if="visionProvider === 'inl_hpc'">
                <label class="label pb-1"><span class="label-text font-medium">Model</span></label>
                <input
                  v-model="visionModel"
                  class="input input-bordered w-full"
                  placeholder="e.g. gpt-oss-120b"
                  @change="saveOCRSettings"
                />
              </div>

              <!-- Ollama model -->
              <div class="form-control" v-else-if="visionProvider === 'ollama'">
                <label class="label pb-1">
                  <span class="label-text font-medium">Model</span>
                  <button class="label-text-alt btn btn-xs btn-ghost" @click="refreshOllamaVisionModels" :disabled="ollamaVisionLoading">
                    {{ ollamaVisionLoading ? '...' : 'Refresh' }}
                  </button>
                </label>
                <select v-if="ollamaVisionModels.length" v-model="visionModel" class="select select-bordered w-full" @change="saveOCRSettings">
                  <option v-for="m in ollamaVisionModels" :key="m.name" :value="m.name">{{ m.name }}</option>
                </select>
                <div v-else-if="ollamaVisionLoading" class="text-xs text-base-content/60 py-2">Checking models...</div>
                <div v-else class="alert alert-warning py-2 text-xs">
                  <span v-if="ollamaVisionTotal > 0">
                    {{ ollamaVisionTotal }} model(s) installed but none support vision. Try: <code>ollama pull qwen2.5-vl</code>
                  </span>
                  <span v-else>Ollama not running or no models installed. <code>ollama pull qwen2.5-vl</code></span>
                </div>
              </div>

              <!-- API Key (cloud providers) -->
              <div class="form-control" v-if="visionProvider !== 'none' && visionProvider !== 'ollama'">
                <label class="label pb-1">
                  <span class="label-text font-medium">API Key (stored on server for indexing)</span>
                  <span v-if="visionKeyFromStorage" class="label-text-alt text-success">loaded from settings</span>
                </label>
                <input
                  v-model="visionApiKey"
                  type="password"
                  class="input input-bordered w-full"
                  :placeholder="visionKeyFromStorage ? '(using saved key)' : 'sk-... or sk-ant-...'"
                  @change="saveOCRSettings"
                />
              </div>

              <!-- Ollama URL -->
              <div class="form-control" v-if="visionProvider === 'ollama'">
                <label class="label pb-1"><span class="label-text font-medium">Ollama URL</span></label>
                <input
                  v-model="visionOllamaUrl"
                  class="input input-bordered w-full"
                  placeholder="http://localhost:11434"
                  @change="saveOCRSettings"
                />
              </div>

              <!-- DPI -->
              <div class="form-control" v-if="visionProvider !== 'none'">
                <label class="label pb-1"><span class="label-text font-medium">Render DPI</span></label>
                <input v-model.number="visionDpi" type="number" min="72" max="400" class="input input-bordered w-full" @change="saveOCRSettings" />
                <label class="label pt-1">
                  <span class="label-text-alt">150 is usually sufficient. Higher = better quality, slower.</span>
                </label>
              </div>
            </div>

            <div v-if="visionProvider !== 'none'" class="mt-4 grid gap-3 sm:grid-cols-2">
              <div class="rounded-xl border border-base-300 bg-base-200/50 p-3">
                <label class="label cursor-pointer justify-start gap-4 p-0">
                  <input type="checkbox" class="toggle toggle-primary toggle-sm" v-model="visionEnhanceImage" @change="saveOCRSettings" />
                  <div>
                    <span class="label-text font-medium">Enhance Image</span>
                    <p class="text-xs text-base-content/60">Boost contrast and sharpness. Recommended for degraded scans.</p>
                  </div>
                </label>
              </div>

              <div class="rounded-xl border border-base-300 bg-base-200/50 p-3">
                <label class="label cursor-pointer justify-start gap-4 p-0">
                  <input type="checkbox" class="toggle toggle-primary toggle-sm" v-model="visionCleanupPass" @change="saveOCRSettings" />
                  <div>
                    <span class="label-text font-medium">LLM Cleanup Pass</span>
                    <p class="text-xs text-base-content/60">Second LLM call to fix OCR errors and remove artifacts. Uses extra tokens.</p>
                  </div>
                </label>
              </div>

              <div class="rounded-xl border border-base-300 bg-base-200/50 p-3">
                <label class="label cursor-pointer justify-start gap-4 p-0">
                  <input type="checkbox" class="toggle toggle-secondary toggle-sm" v-model="visionFormMode" @change="saveOCRSettings" />
                  <div>
                    <span class="label-text font-medium">Form Mode</span>
                    <p class="text-xs text-base-content/60">
                      Removes ruling lines from images and uses a form-aware prompt.
                      Use for scanned government/regulatory forms with boxes and grids.
                    </p>
                  </div>
                </label>
              </div>
            </div>

            <!-- Cleanup model -->
            <div v-if="visionProvider !== 'none' && visionCleanupPass" class="mt-4 rounded-xl border border-base-300 bg-base-100 p-4">
              <h4 class="text-sm font-semibold uppercase tracking-[0.18em] text-base-content/70 mb-2">Cleanup Model</h4>
              <p class="text-xs text-base-content/60 mb-3">Text-only model for the cleanup pass. Leave blank to reuse the vision model.</p>
              <div class="form-control max-w-xs">
                <select v-if="visionProvider === 'openai'" v-model="visionCleanupModel" class="select select-bordered w-full" @change="saveOCRSettings">
                  <option value="">(same as vision model)</option>
                  <option value="gpt-4o">gpt-4o</option>
                  <option value="gpt-4o-mini">gpt-4o-mini</option>
                </select>
                <select v-else-if="visionProvider === 'anthropic'" v-model="visionCleanupModel" class="select select-bordered w-full" @change="saveOCRSettings">
                  <option value="">(same as vision model)</option>
                  <option value="claude-opus-4-6">claude-opus-4-6</option>
                  <option value="claude-sonnet-4-5-20250929">claude-sonnet-4-5</option>
                  <option value="claude-haiku-4-5-20251001">claude-haiku-4-5</option>
                </select>
                <select v-else-if="visionProvider === 'ollama' && ollamaAllModels.length" v-model="visionCleanupModel" class="select select-bordered w-full" @change="saveOCRSettings">
                  <option value="">(same as vision model)</option>
                  <option v-for="m in ollamaAllModels" :key="m.name" :value="m.name">{{ m.name }}</option>
                </select>
                <input v-else-if="visionProvider === 'inl_hpc'" v-model="visionCleanupModel" class="input input-bordered w-full" placeholder="(same as vision model)" @change="saveOCRSettings" />
              </div>
            </div>
          </section>

          <!-- Guardrails -->
          <section class="rounded-xl border border-base-300 bg-base-100 p-4 shadow-sm">
            <div class="mb-4">
              <h4 class="text-sm font-semibold uppercase tracking-[0.18em] text-base-content/70">Guardrails</h4>
              <p class="mt-1 text-xs text-base-content/60">Limit OCR cost and processing time on large documents.</p>
            </div>
            <div class="grid gap-3 md:grid-cols-2">
              <div class="form-control">
                <label class="label pb-1"><span class="label-text font-medium">Max Pages</span></label>
                <input v-model.number="ocrMaxPages" type="number" min="0" class="input input-bordered w-full" @change="saveOCRSettings" />
                <label class="label pt-1"><span class="label-text-alt">0 = no limit.</span></label>
              </div>
              <div class="form-control">
                <label class="label pb-1"><span class="label-text font-medium">Max File Size (MB)</span></label>
                <input v-model.number="ocrMaxFileMb" type="number" min="0" class="input input-bordered w-full" @change="saveOCRSettings" />
                <label class="label pt-1"><span class="label-text-alt">0 = no limit.</span></label>
              </div>
            </div>
          </section>
        </div>

        <div v-if="ocrSettingsSaved" class="alert alert-success py-2 mt-3">
          <span>Settings saved. Applies to new uploads.</span>
        </div>
      </div>
    </div>

    <!-- OCR Preview -->
    <div class="card bg-base-200">
      <div class="card-body">
        <h3 class="card-title">OCR Preview</h3>
        <p class="text-sm text-base-content/70 mb-2">
          Test extraction on a local PDF using the settings above. This preview does not store data.
        </p>

        <div v-if="!ocrEnabled" class="alert alert-warning py-2 mb-3">
          <span>OCR is disabled. Enable it above to test Vision AI or Docling extraction.</span>
        </div>

        <div class="flex flex-wrap gap-2 items-center">
          <button class="btn btn-sm btn-outline" @click="pickOCRFile" :disabled="ocrPlaygroundRunning">
            Choose PDF...
          </button>
          <input
            v-model="ocrPlaygroundPath"
            class="input input-bordered input-sm flex-1 min-w-[320px]"
            placeholder="C:\\path\\to\\scan.pdf"
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
import { ref, watch, onMounted } from 'vue'
import axios from 'axios'

const ocrEnabled = ref(false)
const ocrMaxPages = ref(25)
const ocrMaxFileMb = ref(50)
const ocrSettingsSaved = ref(false)

const visionProvider = ref('none')
const visionModel = ref('')
const visionApiKey = ref('')
const visionKeyFromStorage = ref(false)
const visionOllamaUrl = ref('http://localhost:11434')
const visionDpi = ref(150)
const visionEnhanceImage = ref(true)
const visionCleanupPass = ref(false)
const visionCleanupModel = ref('')
const visionFormMode = ref(false)

const ollamaVisionModels = ref([])
const ollamaVisionLoading = ref(false)
const ollamaVisionTotal = ref(0)
const ollamaAllModels = ref([])

const ocrPlaygroundPath = ref('')
const forceOcr = ref(false)
const normalizePreviewText = ref(false)
const ocrPlaygroundRunning = ref(false)
const ocrPlaygroundError = ref('')
const ocrPlaygroundResult = ref(null)

const DEFAULT_MODELS = {
  openai: 'gpt-4o',
  anthropic: 'claude-opus-4-6',
  ollama: '',
  none: '',
}

const STORAGE_KEY_MAP = {
  openai: 'ai_api_key_openai',
  anthropic: 'ai_api_key_anthropic',
}

const loadVisionKeyFromStorage = (provider) => {
  const storageKey = STORAGE_KEY_MAP[provider]
  if (!storageKey) { visionKeyFromStorage.value = false; return }
  const stored = localStorage.getItem(storageKey)
  if (stored) {
    visionApiKey.value = stored
    visionKeyFromStorage.value = true
  } else {
    visionApiKey.value = ''
    visionKeyFromStorage.value = false
  }
}

const onProviderChange = () => {
  visionModel.value = DEFAULT_MODELS[visionProvider.value] || ''
  visionCleanupModel.value = ''
  loadVisionKeyFromStorage(visionProvider.value)
  if (visionProvider.value === 'ollama') {
    refreshOllamaVisionModels()
    fetchAllOllamaModels()
  }
  saveOCRSettings()
}

const refreshOllamaVisionModels = async () => {
  ollamaVisionLoading.value = true
  try {
    const response = await axios.get('/api/ollama/vision-models')
    ollamaVisionModels.value = response.data.models || []
    ollamaVisionTotal.value = response.data.total_models || 0
    if (ollamaVisionModels.value.length > 0 && !ollamaVisionModels.value.find(m => m.name === visionModel.value)) {
      visionModel.value = ollamaVisionModels.value[0].name
    }
  } catch {
    ollamaVisionModels.value = []
    ollamaVisionTotal.value = 0
  } finally {
    ollamaVisionLoading.value = false
  }
}

const fetchAllOllamaModels = async () => {
  try {
    const response = await axios.get('/api/ollama/status')
    ollamaAllModels.value = response.data.models || []
  } catch {
    ollamaAllModels.value = []
  }
}

const loadOCRSettings = async () => {
  try {
    const response = await axios.get('/api/config')
    ocrEnabled.value = response.data.enable_ocr || false
    ocrMaxPages.value = response.data.ocr_max_pages ?? 25
    ocrMaxFileMb.value = response.data.ocr_max_file_mb ?? 50
    visionProvider.value = response.data.vision_ocr_provider || 'none'
    visionModel.value = response.data.vision_ocr_model || ''
    visionApiKey.value = response.data.vision_ocr_api_key || ''
    visionOllamaUrl.value = response.data.vision_ocr_ollama_url || 'http://localhost:11434'
    visionDpi.value = response.data.vision_ocr_dpi ?? 150
    visionEnhanceImage.value = response.data.vision_ocr_enhance_image ?? true
    visionCleanupPass.value = response.data.vision_ocr_cleanup_pass ?? true
    visionCleanupModel.value = response.data.vision_ocr_cleanup_model || ''
    visionFormMode.value = response.data.vision_ocr_form_mode ?? false

    // If no API key stored on server, try localStorage
    if (!visionApiKey.value) {
      loadVisionKeyFromStorage(visionProvider.value)
    } else {
      visionKeyFromStorage.value = false
    }

    if (visionProvider.value === 'ollama') {
      refreshOllamaVisionModels()
      fetchAllOllamaModels()
    }
  } catch {
    // Use defaults
  }
}

const saveOCRSettings = async () => {
  try {
    await axios.post('/api/config', {
      enable_ocr: ocrEnabled.value,
      ocr_max_pages: Number(ocrMaxPages.value),
      ocr_max_file_mb: Number(ocrMaxFileMb.value),
      vision_ocr_provider: visionProvider.value,
      vision_ocr_model: visionModel.value,
      vision_ocr_api_key: visionApiKey.value,
      vision_ocr_dpi: Number(visionDpi.value),
      vision_ocr_enhance_image: Boolean(visionEnhanceImage.value),
      vision_ocr_cleanup_pass: Boolean(visionCleanupPass.value),
      vision_ocr_cleanup_model: visionCleanupModel.value,
      vision_ocr_ollama_url: visionOllamaUrl.value,
      vision_ocr_form_mode: Boolean(visionFormMode.value),
    })
    ocrSettingsSaved.value = true
    setTimeout(() => { ocrSettingsSaved.value = false }, 5000)
  } catch (error) {
    ocrPlaygroundError.value = error.response?.data?.detail || 'Failed to save OCR settings'
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
    const response = await axios.post('/api/ocr/playground', {
      file_path: ocrPlaygroundPath.value.trim(),
      enable_ocr: ocrEnabled.value,
      ocr_max_pages: Number(ocrMaxPages.value),
      ocr_max_file_mb: Number(ocrMaxFileMb.value),
      vision_ocr_provider: visionProvider.value,
      vision_ocr_model: visionModel.value,
      vision_ocr_api_key: visionApiKey.value,
      vision_ocr_dpi: Number(visionDpi.value),
      vision_ocr_enhance_image: Boolean(visionEnhanceImage.value),
      vision_ocr_cleanup_pass: Boolean(visionCleanupPass.value),
      vision_ocr_cleanup_model: visionCleanupModel.value,
      vision_ocr_ollama_url: visionOllamaUrl.value,
      vision_ocr_form_mode: Boolean(visionFormMode.value),
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
  loadOCRSettings()
})
</script>
