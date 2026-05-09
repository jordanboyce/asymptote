'use strict'

const { app, BrowserWindow, Menu, shell, dialog } = require('electron')
const { spawn, exec } = require('child_process')
const fs = require('fs')
const path = require('path')
const net = require('net')
const http = require('http')

// ─── State ────────────────────────────────────────────────────────────────────

const isDev = process.env.ELECTRON_DEV === 'true' || process.env.NODE_ENV === 'development'

let mainWindow = null
let backendProcess = null
let serverPort = null
let isQuitting = false

// Single-instance lock — clicking the shortcut twice should focus the existing
// window, not spawn a second Electron + backend pair. Without this, orphan
// process pairs accumulate across launches.
if (!app.requestSingleInstanceLock()) {
  app.quit()
  process.exit(0)
}

app.on('second-instance', () => {
  if (!mainWindow) return
  if (mainWindow.isMinimized()) mainWindow.restore()
  if (!mainWindow.isVisible()) mainWindow.show()
  mainWindow.focus()
})

// ─── Port utilities ───────────────────────────────────────────────────────────

function findFreePort(start = 57384) {
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.listen(start, '127.0.0.1', () => {
      const port = server.address().port
      server.close(() => resolve(port))
    })
    server.on('error', () => findFreePort(start + 1).then(resolve).catch(reject))
  })
}

// First launch on a fresh install can take 60–90s: NSIS just dropped ~1.5 GB,
// PyInstaller is unpacking into %TEMP%\_MEI..., AV is scanning DLLs, spaCy +
// sentence-transformers + presidio are loading off cold disk. Warm launches
// take 5–10s. 360 attempts × 500ms = 3 minutes — generous enough for first
// launch on slow disks without waiting forever if the backend genuinely failed.
function waitForServer(port, retries = 360, intervalMs = 500, onProgress = null) {
  return new Promise((resolve, reject) => {
    let attempts = 0
    const check = () => {
      const req = http.get(`http://127.0.0.1:${port}/health`, { timeout: 1000 }, (res) => {
        if (res.statusCode === 200) resolve()
        else retry()
        res.resume()
      })
      req.on('error', retry)
      req.on('timeout', () => { req.destroy(); retry() })
    }
    const retry = () => {
      if (++attempts >= retries) reject(new Error(`Backend did not respond after ${Math.round(retries * intervalMs / 1000)}s`))
      else {
        if (onProgress && attempts % 10 === 0) onProgress(attempts, retries)
        setTimeout(check, intervalMs)
      }
    }
    check()
  })
}

// ─── Paths ────────────────────────────────────────────────────────────────────

function getBackendPath() {
  const exe = process.platform === 'win32' ? 'Finn.exe' : 'Finn'
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'backend', exe)
  }
  // Dev: PyInstaller output at repo root
  return path.join(__dirname, '..', 'dist', 'Finn', exe)
}

function getRendererPath() {
  // The Vue static build lives in electron/renderer/
  return path.join(__dirname, 'renderer', 'index.html')
}

// ─── Backend ──────────────────────────────────────────────────────────────────

async function connectToDevServer() {
  serverPort = 8000
  console.log('[electron] Dev mode: waiting for backend on port 8000')
  await waitForServer(serverPort)
  console.log('[electron] Dev mode: backend ready on port 8000')
}

async function startBackend() {
  serverPort = await findFreePort()
  const backendPath = getBackendPath()

  // Pin user data to Electron's per-user app data dir so it survives launches
  // (the PyInstaller bundle's working dir is wiped on relaunch). This is the
  // single source of truth for where indexes, SQLite, uploads, etc. live.
  const userDataDir = path.join(app.getPath('userData'), 'data')
  try { fs.mkdirSync(userDataDir, { recursive: true }) } catch (_) {}

  console.log(`[electron] Starting backend: ${backendPath} on port ${serverPort}`)
  console.log(`[electron] Backend data dir: ${userDataDir}`)

  backendProcess = spawn(backendPath, ['--no-browser', '--no-tray'], {
    env: {
      ...process.env,
      PORT: String(serverPort),
      FINN_DATA_DIR: userDataDir,
      DATA_DIR: userDataDir,
    },
    stdio: ['ignore', 'pipe', 'pipe'],
    detached: false,
  })

  backendProcess.stdout.on('data', d => console.log(`[backend] ${d.toString().trimEnd()}`))
  backendProcess.stderr.on('data', d => console.error(`[backend] ${d.toString().trimEnd()}`))
  backendProcess.on('exit', (code, signal) => {
    console.log(`[backend] exited — code=${code} signal=${signal}`)
    if (!isQuitting && mainWindow && !mainWindow.isDestroyed()) {
      dialog.showMessageBox(mainWindow, {
        type: 'error',
        title: 'Finn',
        message: 'The Finn backend stopped unexpectedly.',
        detail: 'Please restart the application.',
        buttons: ['OK'],
      })
    }
  })

  await waitForServer(serverPort)
  console.log(`[electron] Backend ready on port ${serverPort}`)
}

// Kill the backend AND any descendants. Node's process.kill on Windows is
// TerminateProcess on the immediate PID only — uvicorn + torch + spaCy can
// fan out to worker processes that survive the parent and pile up as orphan
// Finn.exe instances across sessions. taskkill /T walks the tree; on POSIX
// we fall back to SIGTERM → SIGKILL with a timeout.
function stopBackend() {
  if (!backendProcess || backendProcess.killed) return Promise.resolve()
  const proc = backendProcess
  const pid = proc.pid
  backendProcess = null

  return new Promise((resolve) => {
    let settled = false
    const done = () => { if (!settled) { settled = true; resolve() } }
    proc.once('exit', done)

    // Hard ceiling so a stuck child can't block app quit forever.
    const ceiling = setTimeout(done, 8000)
    proc.once('exit', () => clearTimeout(ceiling))

    if (process.platform === 'win32') {
      exec(`taskkill /F /T /PID ${pid}`, () => { /* exit handler resolves */ })
    } else {
      try { proc.kill('SIGTERM') } catch (_) {}
      setTimeout(() => {
        if (!settled) { try { proc.kill('SIGKILL') } catch (_) {} }
      }, 5000)
    }
  })
}

// ─── Window ───────────────────────────────────────────────────────────────────

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 960,
    minHeight: 640,
    title: 'Finn',
    icon: getAppIcon(),
    backgroundColor: '#0f172a',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      // Pass the chosen port to the preload script
      additionalArguments: [`--api-port=${serverPort}`],
    },
  })

  if (isDev) {
    mainWindow.loadURL('http://localhost:5173')
    mainWindow.webContents.openDevTools()
  } else {
    // Load the Vue app directly from the filesystem — no HTTP server needed
    mainWindow.loadFile(getRendererPath())
  }

  mainWindow.once('ready-to-show', () => {
    mainWindow.show()
    mainWindow.focus()
  })

  // Open external links in the system browser
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url)
    return { action: 'deny' }
  })

  buildAppMenu()
}

function buildAppMenu() {
  const template = [
    ...(process.platform === 'darwin' ? [{
      label: app.name,
      submenu: [
        { role: 'about' },
        { type: 'separator' },
        { role: 'services' },
        { type: 'separator' },
        { role: 'hide' },
        { role: 'hideOthers' },
        { role: 'unhide' },
        { type: 'separator' },
        { role: 'quit' },
      ],
    }] : []),
    {
      label: 'Edit',
      submenu: [
        { role: 'undo' }, { role: 'redo' },
        { type: 'separator' },
        { role: 'cut' }, { role: 'copy' }, { role: 'paste' }, { role: 'selectAll' },
      ],
    },
    {
      label: 'View',
      submenu: [
        { role: 'reload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'resetZoom' }, { role: 'zoomIn' }, { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
      ],
    },
  ]
  Menu.setApplicationMenu(Menu.buildFromTemplate(template))
}

// ─── Icons ────────────────────────────────────────────────────────────────────

function getAppIcon() {
  if (process.platform === 'darwin') {
    const icns = path.join(__dirname, 'assets', 'icon.icns')
    return fs.existsSync(icns) ? icns : path.join(__dirname, 'assets', 'icon.ico')
  }
  if (process.platform === 'win32') return path.join(__dirname, 'assets', 'icon.ico')
  const png = path.join(__dirname, 'assets', 'icon.png')
  return fs.existsSync(png) ? png : path.join(__dirname, 'assets', 'icon.ico')
}

// ─── App lifecycle ────────────────────────────────────────────────────────────

app.whenReady().then(async () => {
  try {
    if (isDev) {
      await connectToDevServer()
    } else {
      await startBackend()
    }
    createWindow()
  } catch (err) {
    console.error('[electron] Startup failed:', err)
    dialog.showErrorBox(
      'Finn failed to start',
      `Could not start the Finn backend.\n\n${err.message}\n\nPlease reinstall the application or contact support.`
    )
    app.quit()
  }
})

app.on('activate', () => {
  mainWindow?.show()
  mainWindow?.focus()
})

app.on('window-all-closed', () => {
  // Closing the last window quits the app — normal desktop behavior. macOS
  // convention is the opposite (apps stay alive until Cmd-Q), so honor that.
  if (process.platform !== 'darwin') app.quit()
})

// Defer Electron's own exit until the backend (and its descendants) are gone.
// Without preventDefault() Electron exits as soon as before-quit returns and
// the kill request races process teardown — which is how orphan Finn.exe
// processes end up surviving the parent.
let cleanupStarted = false
app.on('before-quit', (event) => {
  isQuitting = true
  if (cleanupStarted) return
  cleanupStarted = true
  event.preventDefault()
  stopBackend().finally(() => app.exit(0))
})

// Catch terminal signals in dev (Ctrl+C in the wait-on/electron concurrently
// pane) so the backend gets the same cleanup path.
const onSignal = () => { app.quit() }
process.on('SIGINT', onSignal)
process.on('SIGTERM', onSignal)
