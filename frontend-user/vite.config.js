import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import { VitePWA } from 'vite-plugin-pwa'
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [
      vue(),
      VitePWA({
        registerType: 'autoUpdate',
        manifest: {
          name: '知天气 · 出行助手',
          short_name: '知天气',
          lang: 'zh-CN',
          start_url: '/',
          display: 'standalone',
          theme_color: '#214f44',
          background_color: '#f4f6f2',
          icons: [
            { src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png' },
            { src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png' },
          ],
        },
        workbox: {
          globPatterns: ['**/*.{js,css,html,svg,png,woff2}'],
          navigateFallbackDenylist: [/^\/api\//],
          importScripts: ['/push-sw.js'],
          // Authenticated responses must never be shared through a persistent browser cache.
          runtimeCaching: [],
          cleanupOutdatedCaches: true,
        },
      }),
    ],
    server: {
      host: '127.0.0.1',
      port: 5173,
      proxy: {
        '/api': { target: env.VITE_API_TARGET || 'http://127.0.0.1:8000', changeOrigin: true },
      },
    },
  }
})
