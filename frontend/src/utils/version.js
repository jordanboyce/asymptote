// Minimal semver-ish compare. Returns -1 / 0 / 1 like a comparator.
// Treats missing/non-string inputs as "equal" so the banner stays quiet
// when /api/version reports null/undefined.
export function compareVersions(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string') return 0
  const pa = a.split('-')[0].split('.').map(n => parseInt(n, 10))
  const pb = b.split('-')[0].split('.').map(n => parseInt(n, 10))
  const len = Math.max(pa.length, pb.length)
  for (let i = 0; i < len; i++) {
    const x = Number.isFinite(pa[i]) ? pa[i] : 0
    const y = Number.isFinite(pb[i]) ? pb[i] : 0
    if (x > y) return 1
    if (x < y) return -1
  }
  return 0
}

export function isUpdateAvailable(running, latestKnown) {
  return compareVersions(latestKnown, running) > 0
}
