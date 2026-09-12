<template>
  <div class="space-y-6">
    <header>
      <h1 class="text-xl font-semibold">Connect your AI tools</h1>
      <p class="mt-2 text-sm text-base-content/60 max-w-2xl">
        Search your sources from AnythingLLM, Claude Code, Codex, or VS Code using MCP.
        Your client receives retrieved passages, so choose a client you trust with this data.
      </p>
    </header>

    <!-- Enable toggle -->
    <div class="card bg-base-200">
      <div class="card-body py-4">
        <div class="flex items-center justify-between">
          <div>
            <span class="font-semibold text-sm">MCP Server</span>
            <span :class="mcpSettings.enable_mcp ? 'badge badge-success badge-sm ml-2' : 'badge badge-ghost badge-sm ml-2'">
              {{ mcpSettings.enable_mcp ? 'Enabled' : 'Disabled' }}
            </span>
          </div>
          <input v-if="userStore.adminConsole" v-model="mcpSettings.enable_mcp" aria-label="Enable MCP server" type="checkbox" class="toggle toggle-primary" @change="saveMcpToggle" />
        </div>
      </div>
    </div>

    <div v-if="!mcpSettings.enable_mcp" class="alert">
      <span class="text-sm">{{ userStore.adminConsole ? 'Enable MCP to connect your AI tools.' : 'Ask your administrator to enable MCP for this deployment.' }}</span>
    </div>

    <template v-else>

      <!-- Personal access tokens -->
      <div class="card bg-base-200">
        <div class="card-body space-y-4">
          <div>
            <h3 class="card-title text-base">Personal access tokens</h3>
            <p class="mt-1 text-sm text-base-content/60">
              Create a token for each device. Tokens let clients read the collections you can access.
              You can revoke them here at any time; adding or updating sources is optional.
            </p>
          </div>

          <div v-if="newTokenPlaintext" class="alert alert-warning py-3">
            <div class="space-y-2 w-full">
              <p class="text-sm font-medium">Copy this now — it won't be shown again.</p>
              <div class="flex items-center gap-2">
                <code class="font-mono text-xs bg-base-100 rounded px-2 py-1 flex-1 overflow-x-auto whitespace-nowrap">{{ newTokenPlaintext }}</code>
                <button class="btn btn-xs" @click="copyText(newTokenPlaintext, 'Token')">Copy</button>
                <button class="btn btn-xs btn-ghost" @click="newTokenPlaintext = ''">Dismiss</button>
              </div>
            </div>
          </div>

          <div class="flex items-end gap-2 max-w-md">
            <div class="form-control flex-1">
              <label class="label p-0 pb-1" for="mcp-token-name"><span class="label-text font-medium">Name this connection</span></label>
              <input
                id="mcp-token-name"
                v-model="newTokenName"
                type="text"
                placeholder="e.g. Work laptop — Claude Code"
                class="input input-bordered input-sm w-full"
                @keyup.enter="createToken"
              />
            </div>
            <button class="btn btn-sm btn-primary" :disabled="tokenCreating" @click="createToken">
              <span v-if="tokenCreating" class="loading loading-spinner loading-xs"></span>
              Generate token
            </button>
          </div>

          <!-- Writes are opt-in per token: a leaked read token can search,
               a leaked write token can plant content. -->
          <label class="flex items-start gap-2.5 max-w-xl cursor-pointer">
            <input v-model="newTokenCanWrite" type="checkbox" class="checkbox checkbox-sm checkbox-warning mt-0.5" />
            <span>
              <span class="block text-sm font-medium">Allow adding and updating sources</span>
              <span class="block text-xs text-base-content/55">
                Enables the <code class="font-mono">write_document</code> tool for this token, so an agent can save
                notes, summaries, or markdown into a collection. Off, the token can only read. Collection write
                permissions and the content policy still apply.
              </span>
            </span>
          </label>

          <!-- Restricted collections are invisible to every MCP client unless
               a token is explicitly scoped to them: the grant is made here,
               per token, and shows in the table so it can be revoked knowingly. -->
          <div v-if="restrictedCollections.length" class="rounded-lg border border-error/30 bg-error/5 p-3 max-w-xl">
            <p class="text-xs font-medium flex items-center gap-1.5">
              <ShieldAlert :size="13" class="text-error" aria-hidden="true" />
              Grant this token access to restricted collections
            </p>
            <p class="text-[11px] text-base-content/55 mt-0.5">
              Restricted collections never appear over MCP by default. Tick one to let this specific token reach it.
            </p>
            <div class="flex flex-wrap gap-x-4 gap-y-1 mt-2">
              <label v-for="c in restrictedCollections" :key="c.id" class="flex items-center gap-1.5 text-xs cursor-pointer">
                <input v-model="newTokenScope" type="checkbox" class="checkbox checkbox-xs checkbox-error" :value="c.id" />
                {{ c.name }}
              </label>
            </div>
          </div>

          <div v-if="tokens.length" class="overflow-x-auto">
            <table class="table table-sm">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Token</th>
                  <th>Access</th>
                  <th>Restricted access</th>
                  <th>Created</th>
                  <th>Last used</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="t in tokens" :key="t.id" :class="{ 'opacity-50': t.revoked_at }">
                  <td>{{ t.name }}</td>
                  <td class="font-mono text-xs text-base-content/60">{{ t.token_prefix }}…</td>
                  <td class="text-xs whitespace-nowrap">
                    <span v-if="t.can_write" class="badge badge-xs badge-warning badge-outline">read + write</span>
                    <span v-else class="text-base-content/40">read-only</span>
                  </td>
                  <td class="text-xs">
                    <span v-if="!t.collection_scope || !t.collection_scope.length" class="text-base-content/40">none</span>
                    <span v-else class="flex flex-wrap gap-1">
                      <span v-for="cid in t.collection_scope" :key="cid" class="badge badge-xs badge-error badge-outline">{{ collectionName(cid) }}</span>
                    </span>
                  </td>
                  <td class="text-xs text-base-content/60">{{ formatDate(t.created_at) }}</td>
                  <td class="text-xs text-base-content/60">{{ t.last_used_at ? formatDate(t.last_used_at) : 'Never' }}</td>
                  <td class="text-right">
                    <span v-if="t.revoked_at" class="badge badge-ghost badge-sm">Revoked</span>
                    <button v-else class="btn btn-xs btn-ghost text-error" @click="revokeToken(t)">Revoke</button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="text-xs text-base-content/40">No tokens yet — generate one above to connect an MCP client.</p>
        </div>
      </div>

      <!-- Connect a client -->
      <div class="card bg-base-200">
        <div class="card-body space-y-4">
          <div>
            <h3 class="card-title text-base">Connect a client</h3>
            <p class="mt-1 text-sm text-base-content/60">
              Pick a default collection, then copy the configuration into your client.
              This selects where searches start; it does not restrict the token to that collection.
            </p>
          </div>

          <div v-if="activeTokens.length" class="form-control max-w-xs">
            <label class="label p-0 pb-1" for="mcp-export-token"><span class="label-text font-medium">Token</span></label>
            <select id="mcp-export-token" v-model="exportTokenId" class="select select-bordered select-sm w-full">
              <option v-for="t in activeTokens" :key="t.id" :value="t.id">{{ t.name }}</option>
            </select>
          </div>
          <div v-else class="alert py-2">
            <span class="text-sm">Generate a token above first — the snippets below need one to authenticate.</span>
          </div>

          <div class="form-control max-w-xs">
            <label class="label p-0 pb-1" for="mcp-export-collection"><span class="label-text font-medium">Collection</span></label>
            <select id="mcp-export-collection" v-model="exportCollectionId" class="select select-bordered select-sm w-full">
              <option v-for="c in mcpCollections" :key="c.id" :value="c.id">{{ c.name }}</option>
            </select>
          </div>

          <div class="rounded-lg border border-base-300 bg-base-100 overflow-hidden">
            <!-- Tab bar -->
            <div class="flex flex-wrap border-b border-base-300 bg-base-200/30">
              <button
                v-for="tab in configTabs"
                :key="tab.key"
                class="px-4 py-2 text-xs font-medium transition-colors relative"
                :class="activeTab === tab.key
                  ? 'text-primary border-b-2 border-primary -mb-px bg-base-100/50'
                  : 'text-base-content/50 hover:text-base-content/80'"
                @click="activeTab = tab.key"
              >
                {{ tab.label }}
              </button>
            </div>

            <!-- Config content -->
            <div class="p-3 space-y-2">
              <div class="flex items-center justify-between">
                <span class="text-xs text-base-content/50">{{ activeTabMeta.filename }}</span>
                <div class="flex items-center gap-1">
                  <button
                    class="btn btn-xs btn-ghost gap-1"
                    @click="copyText(activeTabMeta.content, activeTabMeta.label)"
                  >
                    <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                    </svg>
                    Copy
                  </button>
                  <button
                    class="btn btn-xs btn-ghost gap-1"
                    @click="downloadText(activeTabMeta.content, activeTabMeta.filename)"
                  >
                    <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                    </svg>
                    Download
                  </button>
                </div>
              </div>
              <pre
                class="bg-neutral text-neutral-content rounded-lg p-3 text-xs font-mono overflow-x-auto whitespace-pre select-all leading-relaxed"
              >{{ activeTabMeta.content }}</pre>
              <p class="text-xs text-base-content/40">{{ activeTabMeta.hint }}</p>
            </div>
          </div>
        </div>
      </div>

      <!-- Advanced / Global server defaults -->
      <div v-if="userStore.adminConsole" class="card bg-base-200">
        <div class="card-body p-0">
          <details class="group">
            <summary class="cursor-pointer px-5 py-4 text-sm font-medium text-base-content/60 hover:text-base-content select-none list-none flex items-center justify-between">
              <span>Advanced server defaults</span>
              <span class="text-xs text-base-content/40">Search behavior for MCP calls</span>
            </summary>
            <div class="px-5 pb-5 space-y-4 border-t border-base-300 pt-4">
              <p class="text-xs text-base-content/50">
                Defaults applied to MCP tool calls. Clients can override any of them per request
                via URL query params (e.g. <code class="font-mono">?collection_id=…&amp;top_k=…&amp;mode=…</code>).
              </p>
              <div class="grid gap-3 md:grid-cols-2">
                <div class="form-control">
                  <label class="label p-0 pb-1" for="mcp-default-collection"><span class="label-text font-medium">Default collection</span></label>
                  <select id="mcp-default-collection" v-model="mcpSettings.mcp_default_collection" class="select select-bordered w-full">
                    <option v-for="c in mcpCollections" :key="c.id" :value="c.id">{{ c.name }}</option>
                  </select>
                </div>
                <div class="form-control">
                  <label class="label p-0 pb-1" for="mcp-default-mode"><span class="label-text font-medium">Default search mode</span></label>
                  <select id="mcp-default-mode" v-model="mcpSettings.mcp_mode" class="select select-bordered w-full">
                    <option value="semantic">Semantic</option>
                    <option value="keyword">Keyword</option>
                    <option value="hybrid">Hybrid</option>
                  </select>
                </div>
                <div class="form-control">
                  <label class="label p-0 pb-1" for="mcp-default-topk"><span class="label-text font-medium">Default top K</span></label>
                  <input id="mcp-default-topk" v-model.number="mcpSettings.mcp_top_k" type="number" min="1" max="20" class="input input-bordered w-full" />
                </div>
                <div class="form-control">
                  <label class="label p-0 pb-1" for="mcp-excerpt-length"><span class="label-text font-medium">Default excerpt length</span></label>
                  <input id="mcp-excerpt-length" v-model.number="mcpSettings.mcp_max_source_length" type="number" min="100" max="2000" step="50" class="input input-bordered w-full" />
                </div>
              </div>
              <div v-if="mcpSettings.mcp_mode === 'hybrid'" class="form-control">
                <label class="label p-0 pb-1" for="mcp-semantic-weight">
                  <span class="label-text font-medium">Semantic weight</span>
                  <span class="label-text-alt">{{ mcpSettings.mcp_semantic_weight.toFixed(2) }}</span>
                </label>
                <input id="mcp-semantic-weight" v-model.number="mcpSettings.mcp_semantic_weight" type="range" min="0" max="1" step="0.05" class="range range-primary range-sm" :aria-valuetext="`${Math.round(mcpSettings.mcp_semantic_weight * 100)} percent semantic`" />
              </div>
              <label class="label cursor-pointer justify-start gap-3 p-0">
                <input v-model="mcpSettings.mcp_include_sources" type="checkbox" class="checkbox checkbox-sm checkbox-primary" />
                <span class="label-text">Include excerpts by default</span>
              </label>
              <div class="flex justify-end">
                <button class="btn btn-sm btn-primary" @click="saveMcpSettings" :disabled="mcpSaving || mcpLoading">
                  <span v-if="mcpSaving" class="loading loading-spinner loading-xs"></span>
                  Save server defaults
                </button>
              </div>
            </div>
          </details>
        </div>
      </div>

    </template>

    <!-- What this server exposes -->
    <details class="rounded-lg border border-base-300 px-4 py-3 space-y-2">
      <summary class="cursor-pointer text-sm font-medium">Tools and resources reference</summary>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1 text-xs text-base-content/70">
        <div>
          <p class="font-medium text-base-content/80 mb-0.5">Tools (call these)</p>
          <ul class="space-y-0.5 font-mono">
            <li>search_all_collections</li>
            <li>search_collection</li>
            <li>list_recent_documents</li>
            <li>get_document_context</li>
            <li>find_in_documents</li>
            <li>list_tables · query_table</li>
            <li>aggregate_table · get_table_rows</li>
            <li>list_collections · health_check</li>
            <li>write_document <span class="font-sans text-base-content/45">— add or update a source (write-enabled tokens)</span></li>
          </ul>
        </div>
        <div>
          <p class="font-medium text-base-content/80 mb-0.5">Resources (load as context)</p>
          <ul class="space-y-0.5 font-mono">
            <li>collections://all</li>
            <li>collection://&#123;id&#125;</li>
            <li>collection://&#123;id&#125;/guide</li>
            <li>collection://&#123;id&#125;/schema</li>
            <li>collection://&#123;id&#125;/tables</li>
            <li>document://&#123;id&#125;</li>
            <li>table://&#123;id&#125;</li>
          </ul>
        </div>
      </div>
    </details>

    <div v-if="mcpError" class="alert alert-error py-2">
      <span>{{ mcpError }}</span>
    </div>
    <div v-if="mcpStatus" class="alert alert-success py-2">
      <span>{{ mcpStatus }}</span>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ShieldAlert } from 'lucide-vue-next'
import http from '../utils/http'
import { useUserStore } from '../stores/userStore'
const userStore = useUserStore()

// MCP state
const mcpLoading = ref(false)
const mcpSaving = ref(false)
const mcpError = ref('')
const mcpStatus = ref('')
const mcpCollections = ref([])
const exportCollectionId = ref('default')
const activeTab = ref('claude')

// Personal access tokens
const tokens = ref([])
const newTokenName = ref('')
const newTokenScope = ref([])
const newTokenCanWrite = ref(false)
const newTokenPlaintext = ref('')
const tokenCreating = ref(false)
const exportTokenId = ref('')
const mcpSettings = ref({
  enable_mcp: true,
  mcp_default_collection: 'default',
  mcp_top_k: 5,
  mcp_mode: 'semantic',
  mcp_semantic_weight: 0.7,
  mcp_include_sources: true,
  mcp_max_source_length: 500,
})

const configTabs = [
  { key: 'anythingllm', label: 'AnythingLLM' },
  { key: 'claude', label: 'Claude Code' },
  { key: 'codex', label: 'Codex' },
  { key: 'copilot', label: 'GitHub Copilot' },
]

// Client configs are generated locally — the server URL plus a collection_id
// query param is all a client needs; server defaults cover the rest.
const sanitizeServerId = (value) =>
  (value.toLowerCase().replace(/[^a-z0-9-_]+/g, '-').replace(/^[-_]+|[-_]+$/g, '')) || 'asymptote'

const activeTokens = computed(() => tokens.value.filter((t) => !t.revoked_at))
const restrictedCollections = computed(() =>
  mcpCollections.value.filter((c) => c.sensitivity === 'restricted')
)
const collectionName = (cid) => mcpCollections.value.find((c) => c.id === cid)?.name || cid

const serverId = computed(() => sanitizeServerId(`asymptote-${exportCollectionId.value}`))
const serverUrl = computed(() =>
  `${window.location.origin}/mcp/?collection_id=${encodeURIComponent(exportCollectionId.value)}`
)

// The selected token's plaintext is only ever known right after creation.
// Once a page reload happens the server has only the hash, so snippets for
// an older token fall back to a placeholder the user fills in by hand.
const lastCreatedTokenId = ref('')
const selectedTokenPlaintext = computed(() => {
  if (newTokenPlaintext.value && exportTokenId.value === lastCreatedTokenId.value) {
    return newTokenPlaintext.value
  }
  return ''
})

const authHeaderValue = computed(() =>
  selectedTokenPlaintext.value || '<PASTE_YOUR_TOKEN — shown once, right after you generate it>'
)

const activeTabMeta = computed(() => {
  const headers = { 'Authorization': `Bearer ${authHeaderValue.value}` }
  const httpEntry = { type: 'http', url: serverUrl.value, headers }
  const tabs = {
    anythingllm: {
      label: 'AnythingLLM config',
      content: JSON.stringify({ mcpServers: { [serverId.value]: { ...httpEntry, type: 'streamable' } } }, null, 2),
      filename: 'anythingllm_mcp_servers.json',
      hint: 'Merge this entry into anythingllm_mcp_servers.json in your AnythingLLM storage/plugins directory, then reload MCP servers in AnythingLLM. The URL must be reachable from AnythingLLM; localhost inside Docker refers to that container. Keep the credential private.',
    },
    claude: {
      label: 'Claude Code config',
      content: JSON.stringify({ mcpServers: { [serverId.value]: httpEntry } }, null, 2),
      filename: '.mcp.json',
      hint: 'Save as .mcp.json in your project root. This configuration contains a credential; keep it out of version control.',
    },
    codex: {
      label: 'Codex config',
      content: `[mcp_servers.${serverId.value}]\nurl = "${serverUrl.value}"\nhttp_headers = { "Authorization" = "Bearer ${authHeaderValue.value}" }\n`,
      filename: 'config.toml',
      hint: 'Merge this entry into your existing Codex config.toml. Keep the credential private.',
    },
    copilot: {
      label: 'GitHub Copilot config',
      content: JSON.stringify({ servers: { [serverId.value]: httpEntry } }, null, 2),
      filename: '.vscode/mcp.json',
      hint: 'Place this file at .vscode/mcp.json in your project, or merge into your VS Code settings.',
    },
  }
  return tabs[activeTab.value]
})

const loadMcpCollections = async () => {
  try {
    const response = await http.get('/api/collections')
    mcpCollections.value = response.data.collections || []
    if (mcpCollections.value.length > 0 && !mcpCollections.value.find(c => c.id === exportCollectionId.value)) {
      exportCollectionId.value = mcpCollections.value[0].id
    }
  } catch {
    mcpCollections.value = [{ id: 'default', name: 'Default' }]
  }
}

const loadMcpSettings = async () => {
  mcpLoading.value = true
  mcpError.value = ''
  try {
    const response = await http.get('/api/mcp/config')
    mcpSettings.value = { ...mcpSettings.value, ...response.data }
  } catch (error) {
    mcpError.value = error.response?.data?.detail || 'Failed to load MCP settings'
  } finally {
    mcpLoading.value = false
  }
}

const formatDate = (iso) => {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

const loadTokens = async () => {
  try {
    const response = await http.get('/api/mcp/tokens')
    tokens.value = response.data.tokens || []
    if (!activeTokens.value.find((t) => t.id === exportTokenId.value)) {
      exportTokenId.value = activeTokens.value[0]?.id || ''
    }
  } catch (error) {
    mcpError.value = error.response?.data?.detail || 'Failed to load MCP tokens'
  }
}

const createToken = async () => {
  tokenCreating.value = true
  mcpError.value = ''
  try {
    const response = await http.post('/api/mcp/tokens', {
      name: newTokenName.value,
      collection_scope: newTokenScope.value,
      can_write: newTokenCanWrite.value,
    })
    newTokenPlaintext.value = response.data.token
    lastCreatedTokenId.value = response.data.id
    newTokenName.value = ''
    newTokenScope.value = []
    newTokenCanWrite.value = false
    await loadTokens()
    exportTokenId.value = response.data.id
  } catch (error) {
    mcpError.value = error.response?.data?.detail || 'Failed to create MCP token'
  } finally {
    tokenCreating.value = false
  }
}

const revokeToken = async (token) => {
  mcpError.value = ''
  try {
    await http.delete(`/api/mcp/tokens/${token.id}`)
    if (lastCreatedTokenId.value === token.id) {
      newTokenPlaintext.value = ''
      lastCreatedTokenId.value = ''
    }
    await loadTokens()
    mcpStatus.value = `${token.name} revoked`
  } catch (error) {
    mcpError.value = error.response?.data?.detail || 'Failed to revoke token'
  }
}

const copyText = async (value, label) => {
  if (!value) return
  try {
    await navigator.clipboard.writeText(value)
    mcpStatus.value = `${label} copied`
    mcpError.value = ''
  } catch {
    mcpError.value = `Failed to copy ${label}`
  }
}

const downloadText = (value, filename) => {
  if (!value) return
  const blob = new Blob([value], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
  mcpStatus.value = `${filename} downloaded`
}

const saveMcpToggle = async () => {
  mcpError.value = ''
  mcpStatus.value = ''
  try {
    await http.post('/api/mcp/config', { enable_mcp: mcpSettings.value.enable_mcp })
  } catch (error) {
    mcpError.value = error.response?.data?.detail || 'Failed to update MCP status'
    mcpSettings.value.enable_mcp = !mcpSettings.value.enable_mcp
  }
}

const saveMcpSettings = async () => {
  mcpSaving.value = true
  mcpError.value = ''
  mcpStatus.value = ''

  try {
    const payload = {
      ...mcpSettings.value,
      mcp_top_k: Number(mcpSettings.value.mcp_top_k) || 5,
      mcp_semantic_weight: Number(mcpSettings.value.mcp_semantic_weight) || 0.7,
      mcp_max_source_length: Number(mcpSettings.value.mcp_max_source_length) || 500,
    }

    const response = await http.post('/api/mcp/config', payload)
    if (!response.data.success) {
      mcpError.value = response.data.errors?.join(', ') || 'Failed to save MCP settings'
      return
    }

    mcpStatus.value = 'MCP settings saved'
    await loadMcpSettings()
  } catch (error) {
    mcpError.value = error.response?.data?.detail || 'Failed to save MCP settings'
  } finally {
    mcpSaving.value = false
  }
}

onMounted(() => {
  loadMcpCollections()
  loadMcpSettings()
  loadTokens()
})
</script>
