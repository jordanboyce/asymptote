import { createRouter, createWebHistory, createWebHashHistory } from 'vue-router'

// Electron loads the app via file:// which is incompatible with HTML5 history
// mode (no server to handle URL rewrites). Use hash mode in Electron builds,
// history mode everywhere else.
const history = import.meta.env.VITE_ELECTRON
  ? createWebHashHistory()
  : createWebHistory(import.meta.env.BASE_URL)

const router = createRouter({
  history,
  routes: [],
})

export default router
