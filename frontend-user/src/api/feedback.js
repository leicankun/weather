import http from './http'
// 提交反馈（匿名可提交）
export async function createFeedback(payload) {
  return http.post('/feedback', payload)
}
// 我的反馈列表（需登录）
export async function listMyFeedback(page = 1, page_size = 20) {
  return http.get('/feedback', { params: { page, page_size } })
}
