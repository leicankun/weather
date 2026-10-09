import axios from 'axios'
const http = axios.create({ baseURL: '/api/v1', timeout: 30000 })
http.interceptors.request.use((config) => {
  const token = localStorage.getItem('wt_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})
http.interceptors.response.use(
  (response) => {
    const body = response.data
    if (body && typeof body === 'object' && 'code' in body) {
      if (body.code === 0) return body.data
      throw new Error(body.message || '请求失败')
    }
    return body
  },
  (error) => {
    if (error.response?.status === 401 && !error.config?.url?.includes('/users/login')) {
      localStorage.removeItem('wt_token')
      localStorage.removeItem('wt_user')
      window.dispatchEvent(new Event('auth-expired'))
    }
    return Promise.reject(error)
  },
)
export default http
