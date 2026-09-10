// Shared presentation for governance state — one place so the sidebar,
// search results, collection cards, and the admin console agree on what a
// label or a policy status looks like. DaisyUI semantic tokens only.

export const SENSITIVITY_LEVELS = ['public', 'internal', 'confidential', 'restricted']

export const SENSITIVITY_HELP = {
  public: 'Fine for anyone to see.',
  internal: 'Default — for people on this deployment.',
  confidential: 'Handle with care; labelled in every result and citation.',
  restricted: 'Cannot be shared, and invisible to MCP clients unless a token is scoped to it.',
}

export function labelBadgeClass(label) {
  switch (label) {
    case 'public':
      return 'badge-ghost'
    case 'confidential':
      return 'badge-warning badge-outline'
    case 'restricted':
      return 'badge-error badge-outline'
    default:
      return 'badge-ghost'
  }
}

// Whether a label is worth a badge at all — "internal" is the default and
// would just be noise on every row.
export function showLabel(label) {
  return !!label && label !== 'internal'
}

export function policyBadge(status) {
  switch (status) {
    case 'quarantined':
      return { text: 'held', cls: 'badge-error', title: 'Held for review — hidden from search, chat and MCP until an administrator approves it.' }
    case 'flagged':
      return { text: 'flagged', cls: 'badge-warning', title: 'The content-policy scan flagged this document. It is still searchable.' }
    case 'approved':
      return { text: 'reviewed', cls: 'badge-ghost', title: 'Flagged by the scan and approved by an administrator.' }
    default:
      return null
  }
}

// Turn the stored policy_flags into rows the review UI can render.
export function policyFlagPages(flags) {
  if (!flags || !flags.flagged_pages) return []
  return Object.entries(flags.flagged_pages).map(([page, scan]) => ({ page: parseInt(page, 10), ...scan }))
}

export function categoryLabel(cat) {
  return String(cat || '').replace(/_/g, ' ')
}
