import http from './http'

export async function createShare(collectionId, permission = 'read', expiresDays = null, notifyEmail = null) {
  const response = await http.post(`/api/collections/${collectionId}/share`, {
    permission,
    expires_days: expiresDays,
    notify_email: notifyEmail || undefined,
  })
  return response.data
}

export async function listShares(collectionId) {
  const response = await http.get(`/api/collections/${collectionId}/shares`)
  return response.data.shares || []
}

export async function acceptShare(shareToken) {
  const response = await http.post(`/api/shares/${shareToken}/accept`)
  return response.data
}

export async function revokeShare(shareId) {
  const response = await http.delete(`/api/shares/${shareId}`)
  return response.data
}

// ── Edge admissions (admin only) ─────────────────────────────────────────

export async function listAdmissions() {
  const response = await http.get('/api/access/admissions')
  return response.data
}

export async function admitEmail(email) {
  const response = await http.post('/api/access/admissions', { email })
  return response.data
}

export async function withdrawEmail(email) {
  const response = await http.delete(`/api/access/admissions/${encodeURIComponent(email)}`)
  return response.data
}
