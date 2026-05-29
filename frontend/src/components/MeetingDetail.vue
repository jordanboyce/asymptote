<template>
  <div class="border-t border-base-300 px-3 py-3 bg-base-200/30">
    <div v-if="loading" class="text-xs text-base-content/55 italic">Loading…</div>
    <div v-else-if="error" class="text-xs text-error">{{ error }}</div>
    <div v-else-if="detail" class="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">

      <!-- Concerns -->
      <div>
        <div class="text-[11px] font-semibold uppercase tracking-wider text-base-content/55 mb-1">
          Client concerns
        </div>
        <ul v-if="detail.client_concerns.length" class="list-disc list-inside space-y-0.5">
          <li v-for="(c, i) in detail.client_concerns" :key="`c-${i}`" class="text-base-content/85">
            {{ c }}
          </li>
        </ul>
        <p v-else class="text-xs text-base-content/45 italic">None recorded.</p>
      </div>

      <!-- Decisions -->
      <div>
        <div class="text-[11px] font-semibold uppercase tracking-wider text-base-content/55 mb-1">
          Decisions
        </div>
        <ul v-if="detail.decisions.length" class="list-disc list-inside space-y-0.5">
          <li v-for="(d, i) in detail.decisions" :key="`d-${i}`" class="text-base-content/85">
            {{ d }}
          </li>
        </ul>
        <p v-else class="text-xs text-base-content/45 italic">None recorded.</p>
      </div>

      <!-- Follow-ups -->
      <div>
        <div class="text-[11px] font-semibold uppercase tracking-wider text-base-content/55 mb-1">
          Follow-up questions
        </div>
        <ul v-if="detail.follow_up_questions.length" class="list-disc list-inside space-y-0.5">
          <li v-for="(q, i) in detail.follow_up_questions" :key="`q-${i}`" class="text-base-content/85">
            {{ q }}
          </li>
        </ul>
        <p v-else class="text-xs text-base-content/45 italic">None recorded.</p>
      </div>

      <!-- Action items for this meeting -->
      <div>
        <div class="text-[11px] font-semibold uppercase tracking-wider text-base-content/55 mb-1">
          Action items
        </div>
        <ul v-if="detail.action_items.length" class="space-y-0.5">
          <li v-for="(a, i) in detail.action_items" :key="`a-${i}`" class="text-base-content/85 flex items-start gap-1.5">
            <span aria-hidden="true" class="text-base-content/45 leading-none mt-0.5">
              {{ a.status === 'closed' ? '☑' : '☐' }}
            </span>
            <div class="flex-1 min-w-0">
              <div :class="{ 'line-through text-base-content/55': a.status === 'closed' }">
                {{ a.description }}
              </div>
              <div v-if="a.assignee || a.due_date" class="text-[11px] text-base-content/55">
                <span v-if="a.assignee">{{ a.assignee }}</span>
                <span v-if="a.assignee && a.due_date"> · </span>
                <span v-if="a.due_date">due {{ a.due_date }}</span>
              </div>
            </div>
          </li>
        </ul>
        <p v-else class="text-xs text-base-content/45 italic">None recorded.</p>
      </div>

      <!-- Sentiment (spans both columns) -->
      <div v-if="detail.sentiment_notes" class="md:col-span-2 pt-1 border-t border-base-300/60">
        <div class="text-[11px] font-semibold uppercase tracking-wider text-base-content/55 mb-0.5">
          Sentiment
        </div>
        <p class="text-base-content/80 italic">{{ detail.sentiment_notes }}</p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted } from 'vue'
import { getMeetingDetail } from '../utils/meetingsApi'

const props = defineProps({
  collectionId: { type: String, required: true },
  documentId: { type: String, required: true },
})

const detail = ref(null)
const loading = ref(false)
const error = ref('')

// Lazy-load: the <details> element holding this component is closed by
// default, so the network call only fires once the advisor opens the row.
// Vue still mounts the child eagerly, so we fetch on mount and on prop
// change. If load latency becomes an issue, swap to a "fetch on open"
// pattern with @toggle on the parent <details>.
const load = async () => {
  if (!props.collectionId || !props.documentId) return
  loading.value = true
  error.value = ''
  try {
    detail.value = await getMeetingDetail(props.collectionId, props.documentId)
  } catch (e) {
    error.value = e?.response?.data?.detail || e?.message || 'Failed to load meeting.'
  } finally {
    loading.value = false
  }
}

watch(() => [props.collectionId, props.documentId], load)
onMounted(load)
</script>
