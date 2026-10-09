import http from './http'
const TOKEN_KEY = 'wt_token'
const USER_KEY = 'wt_user'
// ===== token / user 本地存储 =====
export function saveAuth(res) {
  localStorage.setItem(TOKEN_KEY, res.access_token)
  localStorage.setItem(USER_KEY, JSON.stringify(res.user))
}
export function getToken() {
  return localStorage.getItem(TOKEN_KEY)
}
export function getStoredUser() {
  const raw = localStorage.getItem(USER_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw)
  } catch {
    return null
  }
}
export function clearAuth() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}
export function isLoggedIn() {
  return !!getToken()
}
// ===== 接口 =====
export async function login(username, password) {
  return http.post('/users/login', { username, password })
}
export async function register(payload) {
  return http.post('/users/register', payload)
}
export async function fetchMe() {
  return http.get('/users/me')
}
/**
 * 更新当前用户资料（昵称 / 邮箱等）。
 *
 * 只允许改自己的资料：user_id 从本地登录态取，避免调用方传错 id 改到别人。
 * 成功后同步本地缓存，刷新后首屏即为新值。
 */
export async function updateProfile(payload) {
  const user = getStoredUser()
  if (!user) throw new Error('未登录')
  const updated = await http.put(`/users/${user.id}`, payload)
  localStorage.setItem(USER_KEY, JSON.stringify({ ...user, ...updated }))
  return updated
}
