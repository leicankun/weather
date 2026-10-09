import http from './http'
/**
 * 取「用户所在区」的天气。
 *
 * 两种入参任选：坐标（浏览器定位，最准）或区名（手动选，也用于刷新时复现）。
 * 返回里的 `location.precision` 必须展示给用户——退到市中心时要说明，
 * 否则用户会以为这就是他家门口的温度。
 */
export async function getLocalWeather(params) {
  return http.get('/weather/local', { params })
}
/** 某城市可选的市辖区列表（供区域选择器使用） */
export async function listDistricts(city = '广州') {
  const res = await http.get('/weather/districts', { params: { city } })
  return res?.items ?? []
}
// 获取最新实测天气
export async function getCurrentWeather(location = 'gz') {
  return http.get('/weather/current', { params: { location } })
}
// 获取 7 天预报
export async function getForecast(location = 'gz') {
  return http.get('/weather/forecast', { params: { location } })
}
// 获取最近 N 条实测记录（过去的天气）
export async function getHistory(limit = 30, location = 'gz') {
  return http.get('/weather/history', { params: { location, limit } })
}
