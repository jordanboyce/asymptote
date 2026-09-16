// Small formatting helpers shared by the sidebar, the collections overview
// and the admin console. Kept dependency-free so they can be unit-tested.

/** 5368709120 → "5 GB", 1288490189 → "1.2 GB", 12288 → "12 KB". Mirrors
 *  services/storage_quota.format_bytes so the UI and API error text agree. */
export function formatBytes(n) {
  const value = Number(n) || 0
  const units = [
    ['TB', 1024 ** 4],
    ['GB', 1024 ** 3],
    ['MB', 1024 ** 2],
    ['KB', 1024],
  ]
  for (const [unit, size] of units) {
    if (value >= size) {
      const v = value / size
      return `${v >= 10 || Number.isInteger(v) ? v.toFixed(0) : v.toFixed(1)} ${unit}`
    }
  }
  return `${value} B`
}

/** "used of limit" with the percentage, for titles and aria labels. */
export function describeStorage(used, limit) {
  if (!limit) return `${formatBytes(used)} used`
  const pct = Math.min(100, Math.round((used / limit) * 100))
  return `${formatBytes(used)} of ${formatBytes(limit)} used (${pct}%)`
}
