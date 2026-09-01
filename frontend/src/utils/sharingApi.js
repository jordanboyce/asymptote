import axios from 'axios'

export async function createShare(collectionId, permission = 'read', expiresDays = null, notifyEmail = null) {
  const response = await axios.post(`/api/collections/${collectionId}/share`, {
    permission,
    expires_days: expiresDays,
    notify_email: notifyEmail || undefined,
  })
  return response.data
}

export async function listShares(collectionId) {
  const response = await axios.get(`/api/collections/${collectionId}/shares`)
  return response.data.shares || []
}

export async function acceptShare(shareToken) {
  const response = await axios.post(`/api/shares/${shareToken}/accept`)
  return response.data
}

export async function revokeShare(shareId) {
  const response = await axios.delete(`/api/shares/${shareId}`)
  return response.data
}

export async function getSharedWithMe() {
  const response = await axios.get('/api/shared-with-me')
  return response.data.collections || []
}

// ── Edge admissions (admin only) ─────────────────────────────────────────

export async function listAdmissions() {
  const response = await axios.get('/api/access/admissions')
  return response.data
}

export async function admitEmail(email) {
  const response = await axios.post('/api/access/admissions', { email })
  return response.data
}

export async function withdrawEmail(email) {
  const response = await axios.delete(`/api/access/admissions/${encodeURIComponent(email)}`)
  return response.data
}
