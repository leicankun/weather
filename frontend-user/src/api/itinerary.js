import http from './http'
// 查询本人行程（可按日期过滤）
export async function listItinerary(date) {
  return http.get('/itinerary', { params: date ? { date } : {} })
}
// 新增行程
export async function createItinerary(payload) {
  return http.post('/itinerary', payload)
}
// 更新行程（只提交需要改的字段）
export async function updateItinerary(id, payload) {
  return http.put(`/itinerary/${id}`, payload)
}
// 删除行程
export async function deleteItinerary(id) {
  return http.delete(`/itinerary/${id}`)
}
/** 生成行程草案（只生成、不落库） */
export async function generateTripPlan(query) {
  // 单独放宽超时：这里要等大模型把整份行程写完，
  // 长假（国庆 7 天 / 春节 8 天）的输出量远超默认 30s 能覆盖的范围
  return http.post('/recommend/plan', { query }, { timeout: 120000 })
}
/** 批量保存行程（AI 排行程的一键保存） */
export async function saveItineraryBatch(items) {
  return http.post('/itinerary/batch', { items })
}
export async function listItineraryRisks() {
  const res = await http.get('/itinerary/risks')
  return res ?? { items: [], by_itinerary: {}, total: 0 }
}
/**
 * 导出 .ics 到系统日历（Day 53）。
 *
 * 用 fetch 而不是 axios 实例：全局响应拦截器按「code/message/data」解包，
 * 日历文件是**纯文本**，走它会当成非法响应。token 从 localStorage 取（与 auth.ts 一致）。
 */
export async function downloadItineraryIcs() {
  const token = localStorage.getItem('wt_token')
  const res = await fetch('/api/v1/itinerary/export.ics', {
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  })
  if (!res.ok) throw new Error('导出失败，请稍后重试')
  const text = await res.text()
  const blob = new Blob([text], { type: 'text/calendar;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = '我的行程.ics'
  a.click()
  URL.revokeObjectURL(url)
}
