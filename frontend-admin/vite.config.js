import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
// https://vite.dev/config/
export default defineConfig(({ mode }) => ({
  plugins: [vue()],
  server: {
    port: 5174,
    proxy: {
      // 开发模式将 /api 代理到本地后端，避免跨域
      '/api': {
        target: loadEnv(mode, process.cwd()).VITE_API_TARGET || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
}))
