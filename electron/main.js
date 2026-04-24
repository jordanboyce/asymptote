'use strict'

const { app, BrowserWindow, Tray, Menu, shell, dialog } = require('electron')
const { spawn } = require('child_process')
const fs = require('fs')
const path = require('path')
const net = require('net')
const http = require('http')

// ─── State ────────────────────────────────────────────────────────────────────

let mainWindow = null
let tray = null
let backendProcess = null
let serverPort = null
let isQuitting = false

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

function waitForServer(port, retries = 60, intervalMs = 500) {
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
      if (++attempts >= retries) reject(new Error(`Backend did not respond after ${retries} attempts`))
      else setTimeout(check, intervalMs)
    }
    check()
  })
}

// ─── Paths ────────────────────────────────────────────────────────────────────

function getBackendPath() {
  const exe = process.platform === 'win32' ? 'Asymptote.exe' : 'Asymptote'
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'backend', exe)
  }
  // Dev: PyInstaller output at repo root
  return path.join(__dirname, '..', 'dist', 'Asymptote', exe)
}

function getRendererPath() {
  // The Vue static build lives in electron/renderer/
  return path.join(__dirname, 'renderer', 'index.html')
}

// ─── Backend ──────────────────────────────────────────────────────────────────

async function startBackend() {
  serverPort = await findFreePort()
  const backendPath = getBackendPath()
  console.log(`[electron] Starting backend: ${backendPath} on port ${serverPort}`)

  backendProcess = spawn(backendPath, ['--no-browser', '--no-tray'], {
    env: {
      ...process.env,
      PORT: String(serverPort),
      // Tell FastAPI not to serve static files — Electron handles the UI
      SERVE_STATIC: 'false',
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
        title: 'Asymptote',
        message: 'The Asymptote backend stopped unexpectedly.',
        detail: 'Please restart the application.',
        buttons: ['OK'],
      })
    }
  })

  await waitForServer(serverPort)
  console.log(`[electron] Backend ready on port ${serverPort}`)
}

function stopBackend() {
  if (backendProcess) {
    try { backendProcess.kill('SIGTERM') } catch (_) {}
    backendProcess = null
  }
}

// ─── Window ───────────────────────────────────────────────────────────────────

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 960,
    minHeight: 640,
    title: 'Asymptote',
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

  // Load the Vue app directly from the filesystem — no HTTP server needed
  mainWindow.loadFile(getRendererPath())

  mainWindow.once('ready-to-show', () => {
    mainWindow.show()
    mainWindow.focus()
  })

  // Open external links in the system browser
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url)
    return { action: 'deny' }
  })

  // Hide to tray instead of quitting
  mainWindow.on('close', (e) => {
    if (!isQuitting) {
      e.preventDefault()
      mainWindow.hide()
    }
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

// ─── Tray ─────────────────────────────────────────────────────────────────────

function createTray() {
  tray = new Tray(getTrayIcon())
  tray.setToolTip('Asymptote')
  tray.setContextMenu(Menu.buildFromTemplate([
    {
      label: 'Open Asymptote',
      click: () => { mainWindow?.show(); mainWindow?.focus() },
    },
    { type: 'separator' },
    {
      label: 'Quit',
      click: () => { isQuitting = true; app.quit() },
    },
  ]))
  tray.on('double-click', () => { mainWindow?.show(); mainWindow?.focus() })
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

function getTrayIcon() {
  if (process.platform === 'darwin') {
    const tray = path.join(__dirname, 'assets', 'tray-icon.png')
    return fs.existsSync(tray) ? tray : path.join(__dirname, 'assets', 'icon.ico')
  }
  if (process.platform === 'win32') return path.join(__dirname, 'assets', 'icon.ico')
  const png = path.join(__dirname, 'assets', 'icon.png')
  return fs.existsSync(png) ? png : path.join(__dirname, 'assets', 'icon.ico')
}

// ─── App lifecycle ────────────────────────────────────────────────────────────

app.whenReady().then(async () => {
  try {
    await startBackend()
    createWindow()
    createTray()
  } catch (err) {
    console.error('[electron] Startup failed:', err)
    dialog.showErrorBox(
      'Asymptote failed to start',
      `Could not start the Asymptote backend.\n\n${err.message}\n\nPlease reinstall the application or contact support.`
    )
    app.quit()
  }
})

app.on('activate', () => {
  mainWindow?.show()
  mainWindow?.focus()
})

app.on('window-all-closed', () => {
  // Stay alive in the tray — don't quit when windows are closed
})

app.on('before-quit', () => {
  isQuitting = true
  stopBackend()
})
