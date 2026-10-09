import { createRouter, createWebHistory } from 'vue-router'
import { getToken } from './api/auth.js'
const pages = [
  ['/', '天气总览', 'Home'],
  ['/chat', 'AI 出行助手', 'Chat'],
  ['/recommend', '出行推荐', 'Recommend'],
  ['/itinerary', '我的行程', 'Itinerary', true],
  ['/notes', '旅行笔记', 'Notes', true],
  ['/news', '天气资讯', 'News'],
  ['/news/:id', '资讯详情', 'NewsDetail'],
  ['/analysis', '天气趋势', 'Analysis', true],
  ['/my-knowledge', '个人知识库', 'MyKnowledge', true],
  ['/notifications', '通知中心', 'Notifications', true],
  ['/profile', '个人设置', 'Profile', true],
  ['/feedback', '意见反馈', 'Feedback'],
  ['/login', '登录 / 注册', 'Login'],
]
const router = createRouter({
  history: createWebHistory(),
  routes: [
    ...pages.map(([path, title, file, auth]) => ({
      path,
      meta: { title, auth },
      component: () => import(`./pages/${file}.vue`),
    })),
    {
      path: '/:pathMatch(.*)*',
      component: () => import('./pages/NotFound.vue'),
      meta: { title: '页面未找到' },
    },
  ],
  scrollBehavior: () => ({ top: 0 }),
})
router.beforeEach((to) => {
  if (to.meta.auth && !getToken()) return { path: '/login', query: { redirect: to.fullPath } }
})
router.afterEach((to) => {
  document.title = `${to.meta.title} · 知天气`
})
export default router
