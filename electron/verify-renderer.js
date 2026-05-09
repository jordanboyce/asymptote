// Fail fast if `electron/renderer/index.html` was emitted with absolute asset
// refs (`src="/assets/..."`). That happens when `vite build` runs without
// `VITE_ELECTRON=true`, leaving Vite's `base` at `/`. Under file:// those refs
// resolve to filesystem root → blank app at runtime (see icon_light.svg → C:\
// 404 hit during 3.0.1 installer testing).

const fs = require('node:fs')
const path = require('node:path')

const indexHtml = path.join(__dirname, 'renderer', 'index.html')
if (!fs.existsSync(indexHtml)) {
  console.error(`ERROR: ${indexHtml} does not exist — frontend build did not run.`)
  process.exit(1)
}

const html = fs.readFileSync(indexHtml, 'utf8')
const absolute = html.match(/\b(?:src|href)="\/assets\/[^"]+/g)
if (absolute) {
  console.error(`ERROR: ${indexHtml} has absolute /assets/ refs:`)
  for (const m of absolute) console.error(`         ${m}`)
  console.error('       VITE_ELECTRON=true was not honored. Rebuild the renderer with')
  console.error('       `cross-env VITE_ELECTRON=true npm run build:electron`.')
  process.exit(1)
}

if (!/\b(?:src|href)="\.\/assets\//.test(html)) {
  console.error(`ERROR: ${indexHtml} has no relative ./assets/ refs — build looks broken.`)
  process.exit(1)
}

console.log('      Renderer paths verified (relative ./assets/...).')
