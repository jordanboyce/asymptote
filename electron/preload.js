'use strict'

const { contextBridge } = require('electron')

// Electron passes the chosen API port via webPreferences.additionalArguments
// as "--api-port=<number>". Read it here so we can expose it to the renderer
// without giving the renderer direct access to Node/Electron internals.
const portArg = process.argv.find(arg => arg.startsWith('--api-port='))
const apiPort = portArg ? portArg.split('=')[1] : null

contextBridge.exposeInMainWorld('asymptote', {
  /** Base URL of the local Python API server, e.g. "http://127.0.0.1:57384" */
  apiUrl: apiPort ? `http://127.0.0.1:${apiPort}` : null,
  /** The host OS — useful for platform-specific UI tweaks in Vue */
  platform: process.platform,
  /** Whether the app is running inside Electron */
  isElectron: true,
})
