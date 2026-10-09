<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from './stores/auth.js'
import AlertStreamListener from './components/AlertStreamListener.vue'
const route = useRoute(),
  router = useRouter(),
  auth = useAuthStore()
const open = ref(false),
  offline = ref(!navigator.onLine)
const nav = [
  ['/', '◉', '天气总览'],
  ['/chat', '✦', 'AI 出行助手'],
  ['/recommend', '↗', '出行推荐'],
  ['/itinerary', '▦', '我的行程'],
  ['/notes', '▤', '旅行笔记'],
  ['/my-knowledge', '◇', '个人知识库'],
  ['/analysis', '⌁', '天气趋势'],
  ['/news', '☷', '天气资讯'],
  ['/notifications', '♧', '通知中心'],
]
function syncNetwork() {
  offline.value = !navigator.onLine
}
function logout() {
  auth.logout()
  router.push('/login')
}
function expired() {
  auth.logout()
  ElMessage.warning('登录已过期，请重新登录')
  if (route.meta.auth) router.push({ path: '/login', query: { redirect: route.fullPath } })
}
onMounted(() => {
  window.addEventListener('online', syncNetwork)
  window.addEventListener('offline', syncNetwork)
  window.addEventListener('auth-expired', expired)
})
onUnmounted(() => {
  window.removeEventListener('online', syncNetwork)
  window.removeEventListener('offline', syncNetwork)
  window.removeEventListener('auth-expired', expired)
})
</script>
<template>
  <div class="app-shell">
    <AlertStreamListener />
    <button v-if="open" class="nav-overlay" aria-label="关闭导航" @click="open = false" />
    <aside class="sidebar" :class="{ open }">
      <RouterLink class="brand" to="/" @click="open = false">
        <span class="brand-icon">☀</span>
        <span>
          知天气
          <small>让每次出发，更有把握</small>
        </span>
      </RouterLink>
      <div class="nav-label">探索与规划</div>
      <nav aria-label="主导航">
        <RouterLink v-for="[path, icon, label] in nav" :key="path" :to="path" @click="open = false">
          <span class="nav-icon">{{ icon }}</span>
          {{ label }}
        </RouterLink>
      </nav>
      <div class="sidebar-bottom">
        <RouterLink to="/feedback" @click="open = false">反馈与建议 ↗</RouterLink>
        <p>天气有变化，计划有余地。</p>
      </div>
    </aside>
    <div class="workspace">
      <header class="topbar">
        <div class="actions">
          <el-button class="mobile-menu" aria-label="打开导航" @click="open = !open">☰</el-button>
          <span class="breadcrumb">
            知天气
            <span>/</span>
            {{ route.meta.title }}
          </span>
        </div>
        <div class="actions">
          <template v-if="auth.loggedIn">
            <RouterLink to="/profile">
              {{ auth.user?.nickname || auth.user?.username || '个人设置' }}
            </RouterLink>
            <el-button text @click="logout">退出</el-button>
          </template>
          <RouterLink v-else class="login-link" to="/login">登录 / 注册 ↗</RouterLink>
        </div>
      </header>
      <div v-if="offline" class="offline-banner" role="status">
        当前处于离线状态，联网后可刷新天气和对话。
      </div>
      <main class="main-content"><RouterView /></main>
      <footer class="footer">
        知天气 · 天气与出行助手
        <span>天气数据以最新预报为准</span>
      </footer>
    </div>
  </div>
</template>
