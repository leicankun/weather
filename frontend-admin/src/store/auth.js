import { defineStore } from 'pinia'
import http, { clearAdminAuth, getAdminToken, getAdminUser, setAdminAuth } from '../api/http'
export const useAuthStore = defineStore('auth', {
  state: () => ({
    user: getAdminUser(),
    token: getAdminToken(),
  }),
  getters: {
    isLoggedIn: (s) => !!s.token,
    isAdmin: (s) => s.user?.role === 'admin',
    displayName: (s) => s.user?.nickname || s.user?.username || '管理员',
  },
  actions: {
    async login(username, password) {
      const res = await http.post('/users/login', { username, password })
      setAdminAuth(res.access_token, res.user)
      this.token = res.access_token
      this.user = res.user
      return res
    },
    logout() {
      clearAdminAuth()
      this.token = null
      this.user = null
    },
  },
})
