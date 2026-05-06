import axios from 'axios'

export async function createShare(collectionId, permission = 'read', expiresDays = null) {
  const response = await axios.post(`/api/collections/${collectionId}/share`, {
    permission,
    expires_days: expiresDays,
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
