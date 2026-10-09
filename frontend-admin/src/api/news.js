import http from './http'
// 资讯/公告列表（含未发布）
export function listNews(params = {}) {
  return http.get('/news', {
    params: {
      page: 1,
      page_size: 20,
      published_only: false,
      ...params,
    },
  })
}
export function createNews(payload) {
  return http.post('/news', payload)
}
export function updateNews(id, payload) {
  return http.put(`/news/${id}`, payload)
}
export function deleteNews(id) {
  return http.delete(`/news/${id}`)
}
