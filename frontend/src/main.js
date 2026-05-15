import { createApp } from 'vue'
import { createPinia } from 'pinia'
import VueApexCharts from 'vue3-apexcharts'
import axios from 'axios'
import './style.css'

import App from './App.vue'
import router from './router'

// When running inside Electron the preload bridge exposes window.finn.apiUrl
// (e.g. "http://127.0.0.1:57384"). Setting it as axios's baseURL means all
// relative-path calls like axios.get('/api/collections') resolve correctly even
// when the page is loaded via file://.
// In browser / dev-server mode window.finn is undefined and we fall back to
// '' so relative paths continue to work against the same origin.
const apiBase = window.finn?.apiUrl ?? ''
if (apiBase) {
  axios.defaults.baseURL = apiBase
}

const app = createApp(App)

app.use(createPinia())
app.use(router)
app.use(VueApexCharts)

app.mount('#app')
