// Slice C — meetings + action items.
//
// Thin axios wrappers around:
//   GET    /api/collections/{id}/meetings
//   GET    /api/collections/{id}/meetings/{document_id}
//   GET    /api/collections/{id}/action-items
//   POST   /api/collections/{id}/action-items
//   PATCH  /api/collections/{id}/action-items/{item_id}
//   DELETE /api/collections/{id}/action-items/{item_id}
//
// The legacy `/meetings/action-items` URL still works server-side (the brief
// modal calls it); new callers use `/action-items` which includes the
// chat-originated standalone items too.

import axios from 'axios'

export async function listMeetings(collectionId) {
  const { data } = await axios.get(`/api/collections/${collectionId}/meetings`)
  return data
}

export async function getMeetingDetail(collectionId, documentId) {
  const { data } = await axios.get(
    `/api/collections/${collectionId}/meetings/${encodeURIComponent(documentId)}`,
  )
  return data
}

export async function listActionItems(collectionId, { status = 'open', assignee = null, limit = 100 } = {}) {
  const params = { limit }
  if (status != null) params.status = status
  if (assignee) params.assignee = assignee
  const { data } = await axios.get(`/api/collections/${collectionId}/action-items`, { params })
  return data
}

export async function createActionItem(collectionId, payload) {
  const { data } = await axios.post(`/api/collections/${collectionId}/action-items`, payload)
  return data
}

export async function updateActionItem(collectionId, itemId, patch) {
  const { data } = await axios.patch(
    `/api/collections/${collectionId}/action-items/${encodeURIComponent(itemId)}`,
    patch,
  )
  return data
}

export async function deleteActionItem(collectionId, itemId) {
  await axios.delete(
    `/api/collections/${collectionId}/action-items/${encodeURIComponent(itemId)}`,
  )
}
