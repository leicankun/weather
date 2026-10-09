import http from './http'
// 管理端气象时序分页查询
export function adminList(params = {}) {
  return http.get('/weather/admin-list', { params: { page: 1, page_size: 20, ...params } })
}
// 手动同步
export function syncNow() {
  return http.post('/weather/sync')
}
// 历史回补（同步过去 N 天）
export function syncHistory(days = 30) {
  return http.post(`/weather/sync-history?days=${days}`)
}
// 当前最新实测
export function currentWeather() {
  return http.get('/weather/current')
}
// 7 天预报
export function forecast() {
  return http.get('/weather/forecast')
}
// 最近实测（history）
export function historyList(limit = 30) {
  return http.get('/weather/history', { params: { limit } })
}
export function weatherStats(days = 30) {
  return http.get('/weather/stats', { params: { days } })
}
/** 日粒度趋势序列（趋势图数据源） */
export function analysisDaily(days = 90, location = 'gz') {
  return http.get('/weather/analysis/daily', { params: { days, location } })
}
/** 月度同比 / 环比 */
export function analysisCompare(kind, year, month, location = 'gz') {
  const params = { kind, location }
  if (year) params.year = year
  if (month) params.month = month
  return http.get('/weather/analysis/compare', { params })
}
/** 手动刷新连续聚合（回补历史后立即生效用） */
export function refreshAggregate(since) {
  return http.post('/weather/analysis/refresh', { params: since ? { since } : {} })
}
/** 回补历史日统计（Open-Meteo 归档），同比需要去年同期数据 */
export function syncArchive(start, end, location = 'gz') {
  return http.post('/weather/sync-archive', { params: { start, end, location } })
}
