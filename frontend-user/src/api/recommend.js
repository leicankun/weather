import http from './http'
export async function recommendTravel(origin, destination, city, coords) {
  return http.get('/recommend/travel', {
    params: { origin, destination, city: city ?? undefined, ...coords },
  })
}
export async function recommendOutfit(city, scene, preference) {
  return http.get('/recommend/outfit', {
    params: {
      city: city ?? undefined,
      scene: scene ?? undefined,
      preference: preference ?? undefined,
    },
  })
}
// 穿搭灵感：独立接口，前端可先渲染规则建议再异步加载（搜索耗时数秒）
export async function getOutfitPosts(city, scene, preference) {
  return http.get('/recommend/outfit/posts', {
    params: {
      city: city ?? undefined,
      scene: scene ?? undefined,
      preference: preference ?? undefined,
    },
  })
}
export async function getWeatherHead(city) {
  return http.get('/recommend/weather-head', {
    params: { city: city ?? undefined },
  })
}
