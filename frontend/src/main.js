import { createApp } from 'vue'
import { createPinia } from 'pinia'
import './style.css'
import { migrateBrowserStorage } from './utils/storageMigration'

import App from './App.vue'
import RegisterPage from './components/RegisterPage.vue'

// The bundle serves two entry points from one build: the workspace at "/"
// and the public registration page at "/register" (the backend serves
// index.html for both, and exempts the latter plus the assets from auth).
// A router would be overkill for one extra route that shares nothing with
// the workspace shell.
// Carry browser state over from the pre-rename key names before any store
// (Pinia or module-level) reads it.
migrateBrowserStorage()

const path = window.location.pathname.replace(/\/+$/, '')
const Root = path === '/register' ? RegisterPage : App

const app = createApp(Root)

app.use(createPinia())

app.mount('#app')
