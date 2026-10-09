import http from './http'
// axios 拦截器已把统一响应体解包成 data。
export async function suggestPlaces(keyword, city) {
  const res = await http.get('/places/suggest', {
    params: { keyword, city: city ?? undefined },
  })
  return res?.items ?? []
}
// 常用地点（内置广州地标，零依赖兜底）
export async function listLandmarks() {
  const res = await http.get('/places/landmarks')
  return res?.items ?? []
}
