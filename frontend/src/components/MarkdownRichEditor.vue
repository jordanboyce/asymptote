<template>
  <div
    class="border border-base-300 rounded-lg bg-base-100 focus-within:border-primary transition-colors overflow-hidden"
    :class="{ 'border-error focus-within:border-error': error }"
  >
    <!-- Toolbar -->
    <div
      v-if="editor"
      class="flex items-center gap-0.5 flex-wrap px-2 py-1.5 border-b border-base-300 bg-base-200/50"
    >
      <button
        type="button"
        class="btn btn-ghost btn-xs px-2"
        :class="{ 'btn-active': editor.isActive('heading', { level: 2 }) }"
        title="Section heading"
        @click="editor.chain().focus().toggleHeading({ level: 2 }).run()"
      >
        <Heading2 :size="14" />
      </button>
      <button
        type="button"
        class="btn btn-ghost btn-xs px-2"
        :class="{ 'btn-active': editor.isActive('heading', { level: 3 }) }"
        title="Subheading"
        @click="editor.chain().focus().toggleHeading({ level: 3 }).run()"
      >
        <Heading3 :size="14" />
      </button>
      <div class="w-px h-4 bg-base-300 mx-1" />
      <button
        type="button"
        class="btn btn-ghost btn-xs px-2"
        :class="{ 'btn-active': editor.isActive('bold') }"
        title="Bold"
        @click="editor.chain().focus().toggleBold().run()"
      >
        <Bold :size="14" />
      </button>
      <button
        type="button"
        class="btn btn-ghost btn-xs px-2"
        :class="{ 'btn-active': editor.isActive('italic') }"
        title="Italic"
        @click="editor.chain().focus().toggleItalic().run()"
      >
        <Italic :size="14" />
      </button>
      <button
        type="button"
        class="btn btn-ghost btn-xs px-2"
        :class="{ 'btn-active': editor.isActive('code') }"
        title="Inline code"
        @click="editor.chain().focus().toggleCode().run()"
      >
        <Code :size="14" />
      </button>
      <div class="w-px h-4 bg-base-300 mx-1" />
      <button
        type="button"
        class="btn btn-ghost btn-xs px-2"
        :class="{ 'btn-active': editor.isActive('bulletList') }"
        title="Bullet list"
        @click="editor.chain().focus().toggleBulletList().run()"
      >
        <List :size="14" />
      </button>
      <button
        type="button"
        class="btn btn-ghost btn-xs px-2"
        :class="{ 'btn-active': editor.isActive('orderedList') }"
        title="Numbered list"
        @click="editor.chain().focus().toggleOrderedList().run()"
      >
        <ListOrdered :size="14" />
      </button>
      <button
        type="button"
        class="btn btn-ghost btn-xs px-2"
        :class="{ 'btn-active': editor.isActive('blockquote') }"
        title="Quote"
        @click="editor.chain().focus().toggleBlockquote().run()"
      >
        <Quote :size="14" />
      </button>
      <div class="w-px h-4 bg-base-300 mx-1" />
      <button
        type="button"
        class="btn btn-ghost btn-xs px-2"
        :class="{ 'btn-active': editor.isActive('link') }"
        title="Link"
        @click="setLink"
      >
        <LinkIcon :size="14" />
      </button>
      <div class="flex-1" />
      <button
        type="button"
        class="btn btn-ghost btn-xs px-2"
        title="Undo"
        :disabled="!editor.can().undo()"
        @click="editor.chain().focus().undo().run()"
      >
        <Undo2 :size="14" />
      </button>
      <button
        type="button"
        class="btn btn-ghost btn-xs px-2"
        title="Redo"
        :disabled="!editor.can().redo()"
        @click="editor.chain().focus().redo().run()"
      >
        <Redo2 :size="14" />
      </button>
    </div>

    <!-- Editor surface -->
    <EditorContent
      :editor="editor"
      class="markdown-editor-surface px-4 py-3 max-h-96 overflow-y-auto"
    />
  </div>
</template>

<script setup>
import { watch, onBeforeUnmount } from 'vue'
import { useEditor, EditorContent } from '@tiptap/vue-3'
import StarterKit from '@tiptap/starter-kit'
import Link from '@tiptap/extension-link'
import Placeholder from '@tiptap/extension-placeholder'
import { Markdown } from 'tiptap-markdown'
import {
  Bold, Italic, Code, List, ListOrdered, Quote,
  Heading2, Heading3, Link as LinkIcon, Undo2, Redo2,
} from 'lucide-vue-next'

const props = defineProps({
  modelValue: { type: String, default: '' },
  placeholder: { type: String, default: '' },
  error: { type: Boolean, default: false },
})

const emit = defineEmits(['update:modelValue'])

const editor = useEditor({
  content: props.modelValue,
  extensions: [
    StarterKit.configure({
      // StarterKit's link is disabled by default; we add the extension below
      // so we can configure openOnClick: false (advisors edit, not browse).
      heading: { levels: [2, 3] },
    }),
    Link.configure({
      openOnClick: false,
      autolink: true,
      HTMLAttributes: { class: 'link link-primary' },
    }),
    Markdown.configure({
      // Tight markdown — no html passthrough so the body stays clean for the
      // downstream prompt; transformPastedText so users can paste markdown
      // from another doc and have it parse rather than appear as literal `##`.
      html: false,
      transformPastedText: true,
      transformCopiedText: false,
    }),
    Placeholder.configure({
      placeholder: () => props.placeholder || '',
    }),
  ],
  editorProps: {
    attributes: {
      class: 'prose prose-sm max-w-none focus:outline-none min-h-[14rem]',
    },
  },
  onUpdate: ({ editor }) => {
    const md = editor.storage.markdown.getMarkdown()
    emit('update:modelValue', md)
  },
})

// Sync external changes (e.g. AI generator hands a draft body to the parent).
// Only re-set content when the incoming value differs from what the editor
// already serializes — otherwise every keystroke would round-trip and reset
// the cursor.
watch(() => props.modelValue, (next) => {
  if (!editor.value) return
  const current = editor.value.storage.markdown.getMarkdown()
  if ((next || '') === (current || '')) return
  editor.value.commands.setContent(next || '', false)
})

function setLink() {
  if (!editor.value) return
  const previous = editor.value.getAttributes('link').href || ''
  const url = window.prompt('URL (leave blank to remove):', previous)
  if (url === null) return
  if (url === '') {
    editor.value.chain().focus().extendMarkRange('link').unsetLink().run()
    return
  }
  editor.value.chain().focus().extendMarkRange('link').setLink({ href: url }).run()
}

onBeforeUnmount(() => {
  editor.value?.destroy()
})
</script>

<style>
/* Theme-agnostic editor styling: inherit color from base-content, use
   semantic tokens only. The .prose class from Tailwind Typography would
   collide with DaisyUI themes, so we override the bits we care about. */
.markdown-editor-surface .ProseMirror {
  color: hsl(var(--bc));
  font-size: 0.9rem;
  line-height: 1.6;
}
.markdown-editor-surface .ProseMirror:focus { outline: none; }
.markdown-editor-surface .ProseMirror > * + * { margin-top: 0.6em; }
.markdown-editor-surface .ProseMirror h2 {
  font-size: 1.05rem;
  font-weight: 700;
  margin-top: 1.1em;
  margin-bottom: 0.3em;
}
.markdown-editor-surface .ProseMirror h3 {
  font-size: 0.95rem;
  font-weight: 600;
  margin-top: 0.9em;
  margin-bottom: 0.2em;
}
.markdown-editor-surface .ProseMirror ul,
.markdown-editor-surface .ProseMirror ol {
  padding-left: 1.4em;
}
.markdown-editor-surface .ProseMirror ul { list-style: disc; }
.markdown-editor-surface .ProseMirror ol { list-style: decimal; }
.markdown-editor-surface .ProseMirror li > p { margin: 0; }
.markdown-editor-surface .ProseMirror blockquote {
  border-left: 3px solid hsl(var(--bc) / 0.2);
  padding-left: 0.8em;
  color: hsl(var(--bc) / 0.75);
  font-style: italic;
}
.markdown-editor-surface .ProseMirror code {
  background: hsl(var(--b2));
  padding: 0.1em 0.3em;
  border-radius: 0.25rem;
  font-size: 0.85em;
}
.markdown-editor-surface .ProseMirror p.is-editor-empty:first-child::before {
  color: hsl(var(--bc) / 0.4);
  content: attr(data-placeholder);
  float: left;
  height: 0;
  pointer-events: none;
}
</style>
