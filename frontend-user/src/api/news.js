import http from './http'
// 资讯列表（公开，只取已发布）
export async function listNews(params = {}) {
  return http.get('/news', {
    params: {
      page: params.page ?? 1,
      page_size: params.page_size ?? 12,
      category: params.category ?? undefined,
      keyword: params.keyword ?? undefined,
      published_only: params.published_only ?? true,
    },
  })
}
// 资讯详情（公开，阅读量+1）
export async function getNews(id) {
  return http.get(`/news/${id}`)
}
