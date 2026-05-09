<template>
  <div class="card bg-base-200">
    <div class="card-body space-y-4">

      <!-- Editor header -->
      <div class="flex items-center justify-between gap-2">
        <h3 class="card-title text-base">
          {{ isNew ? 'New Expertise Pack' : 'Edit Pack' }}
        </h3>
        <button class="btn btn-ghost btn-sm" @click="$emit('cancel')">
          <X :size="16" />
        </button>
      </div>

      <!-- Name -->
      <div class="form-control">
        <label class="label pb-1">
          <span class="label-text font-medium">Name <span class="text-error">*</span></span>
        </label>
        <input
          v-model="form.name"
          type="text"
          placeholder="e.g. Conservative Income Policy"
          class="input input-bordered w-full"
          :class="{ 'input-error': errors.name }"
          @input="errors.name = ''"
        />
        <label v-if="errors.name" class="label pt-1">
          <span class="label-text-alt text-error">{{ errors.name }}</span>
        </label>
      </div>

      <!-- Description -->
      <div class="form-control">
        <label class="label pb-1">
          <span class="label-text font-medium">Description</span>
          <span class="label-text-alt text-base-content/50">optional — shown in the library list</span>
        </label>
        <input
          v-model="form.description"
          type="text"
          placeholder="One-line summary of this pack's purpose"
          class="input input-bordered w-full"
        />
      </div>

      <!-- Body -->
      <div class="form-control">
        <label class="label pb-1">
          <span class="label-text font-medium">Guidance body <span class="text-error">*</span></span>
          <span class="label-text-alt text-base-content/50">Use sections and bullets — injected into the AI system prompt</span>
        </label>
        <MarkdownRichEditor
          v-model="form.body"
          :error="!!errors.body"
          placeholder="Write your advisor guidelines, investment policies, or analysis frameworks. Use Section heading (H2) to label parts the AI should latch onto, and bullets for directives. Example: a 'Heuristics' section with bullets like 'Flag any single position over 5% of household NAV'."
          @update:modelValue="errors.body = ''"
        />
        <label v-if="errors.body" class="label pt-1">
          <span class="label-text-alt text-error">{{ errors.body }}</span>
        </label>
      </div>

      <!-- Character count hint -->
      <p class="text-xs text-base-content/40 -mt-2">
        {{ form.body.length.toLocaleString() }} characters
        <span v-if="form.body.length > 8000" class="text-warning ml-1">
          — keep packs focused to avoid using too much context budget
        </span>
      </p>

      <!-- Actions -->
      <div class="flex items-center justify-between pt-2">
        <div>
          <button
            v-if="!isNew"
            class="btn btn-ghost btn-sm text-error"
            :disabled="saving"
            @click="handleDelete"
          >
            <Trash2 :size="14" />
            Delete pack
          </button>
        </div>
        <div class="flex gap-2">
          <button class="btn btn-ghost btn-sm" :disabled="saving" @click="$emit('cancel')">
            Cancel
          </button>
          <button class="btn btn-primary btn-sm" :disabled="saving" @click="handleSave">
            <span v-if="saving" class="loading loading-spinner loading-xs" />
            <Save v-else :size="14" />
            {{ isNew ? 'Create Pack' : 'Save Changes' }}
          </button>
        </div>
      </div>

      <!-- Inline error -->
      <div v-if="saveError" class="alert alert-error py-2 text-sm">
        {{ saveError }}
      </div>

    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { X, Save, Trash2 } from 'lucide-vue-next'
import { useExpertiseStore } from '../stores/expertiseStore'
import MarkdownRichEditor from './MarkdownRichEditor.vue'

const props = defineProps({
  pack: {
    type: Object,
    default: null,   // null = creating new
  },
})

const emit = defineEmits(['save', 'cancel', 'delete'])

const store = useExpertiseStore()

// "New" means no persisted id yet — covers both a null prop (clean create) and
// a pre-filled draft object (e.g. handed over from the AI generator) that has
// no id. The save path branches on this to call create vs. update.
const isNew = computed(() => !props.pack?.id)

const form = ref({
  name: props.pack?.name ?? '',
  description: props.pack?.description ?? '',
  body: props.pack?.body ?? '',
})

const errors = ref({ name: '', body: '' })
const saving = ref(false)
const saveError = ref('')

// Keep form in sync if parent swaps the pack prop
watch(() => props.pack, (p) => {
  form.value = {
    name: p?.name ?? '',
    description: p?.description ?? '',
    body: p?.body ?? '',
  }
  errors.value = { name: '', body: '' }
  saveError.value = ''
})

function validate() {
  let ok = true
  if (!form.value.name.trim()) {
    errors.value.name = 'Name is required'
    ok = false
  }
  if (!form.value.body.trim()) {
    errors.value.body = 'Guidance body is required'
    ok = false
  }
  return ok
}

async function handleSave() {
  if (!validate()) return
  saving.value = true
  saveError.value = ''
  try {
    const payload = {
      name: form.value.name.trim(),
      description: form.value.description.trim() || null,
      body: form.value.body.trim(),
    }
    if (isNew.value) {
      await store.createPack(payload)
    } else {
      await store.updatePack(props.pack.id, payload)
    }
    emit('save')
  } catch (err) {
    saveError.value = err.response?.data?.detail || err.message || 'Save failed'
  } finally {
    saving.value = false
  }
}

async function handleDelete() {
  if (!props.pack) return
  saving.value = true
  saveError.value = ''
  try {
    await store.deletePack(props.pack.id)
    emit('delete')
  } catch (err) {
    saveError.value = err.response?.data?.detail || err.message || 'Delete failed'
  } finally {
    saving.value = false
  }
}
</script>
