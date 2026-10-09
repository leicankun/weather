import http from './http'
export function listFeedback(page = 1, page_size = 20, status) {
  return http.get('/feedback', { params: { page, page_size, status_filter: status || undefined } })
}
export function updateFeedback(id, payload) {
  return http.put(`/feedback/${id}`, payload)
}
