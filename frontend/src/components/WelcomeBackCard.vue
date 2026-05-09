<template>
  <div
    v-if="show"
    class="fixed inset-0 z-[280] bg-base-200/80 backdrop-blur-sm flex items-center justify-center p-6"
    role="dialog"
    aria-modal="true"
    aria-labelledby="welcome-back-title"
  >
    <div class="max-w-lg w-full bg-base-100 border border-base-300 rounded-lg shadow-lg p-6 space-y-5">
      <div>
        <h2 id="welcome-back-title" class="text-xl font-semibold tracking-tight">
          Welcome back
        </h2>
        <p class="text-sm text-base-content/65 mt-2 leading-relaxed">
          It's been {{ daysLabel }} since you last opened Finn. Here's a quick reminder of what you can do.
        </p>
      </div>

      <ul class="space-y-3 text-sm">
        <li class="flex gap-3">
          <FileText :size="18" class="text-primary flex-shrink-0 mt-0.5" aria-hidden="true" />
          <div>
            <div class="font-medium">Generate a Meeting Brief</div>
            <p class="text-base-content/60 text-xs mt-0.5">
              Open a client collection and click "Generate Meeting Brief" in the Studio panel for a one-page pre-meeting summary.
            </p>
          </div>
        </li>
        <li class="flex gap-3">
          <MessageSquare :size="18" class="text-primary flex-shrink-0 mt-0.5" aria-hidden="true" />
          <div>
            <div class="font-medium">Ask portfolio questions in chat</div>
            <p class="text-base-content/60 text-xs mt-0.5">
              Pick a collection, then ask "What's the cash position?" or "Show top 10 holdings" — Finn answers from local data with PII redacted.
            </p>
          </div>
        </li>
        <li class="flex gap-3">
          <Mic :size="18" class="text-primary flex-shrink-0 mt-0.5" aria-hidden="true" />
          <div>
            <div class="font-medium">Record a meeting and draft a Note of Record</div>
            <p class="text-base-content/60 text-xs mt-0.5">
              Use the header Record button during a client meeting. After it transcribes, "Draft Note of Record" turns it into a compliance-ready note.
            </p>
          </div>
        </li>
      </ul>

      <div class="flex justify-end pt-1">
        <button class="btn btn-primary btn-sm" @click="$emit('dismiss')">
          Got it
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { FileText, MessageSquare, Mic } from 'lucide-vue-next'

const props = defineProps({
  show: { type: Boolean, default: false },
  days: { type: Number, default: null },
})

defineEmits(['dismiss'])

const daysLabel = computed(() => {
  if (typeof props.days !== 'number' || !Number.isFinite(props.days)) return 'a while'
  const rounded = Math.floor(props.days)
  if (rounded < 1) return 'less than a day'
  if (rounded === 1) return '1 day'
  return `${rounded} days`
})
</script>
