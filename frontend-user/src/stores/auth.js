import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { getStoredUser, getToken, saveAuth, clearAuth, fetchMe } from '../api/auth.js'
export const useAuthStore = defineStore('auth', () => {
  const user = ref(getStoredUser()),
    token = ref(getToken())
  const loggedIn = computed(() => !!token.value)
  function accept(result) {
    saveAuth(result)
    user.value = result.user
    token.value = result.access_token
  }
  function logout() {
    clearAuth()
    user.value = null
    token.value = null
  }
  async function refresh() {
    user.value = await fetchMe()
    localStorage.setItem('wt_user', JSON.stringify(user.value))
  }
  return { user, token, loggedIn, accept, logout, refresh }
})
