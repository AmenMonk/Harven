// HarvenAI SPA 构建配置 — 总方案 B0
// 产物进 static/spa/，由 Flask 静态托管（/static/spa/），旧 templates 界面灰度并存
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  base: '/static/spa/',
  build: {
    outDir: '../static/spa',
    emptyOutDir: true,
  },
  server: {
    port: 5188,
    proxy: {
      // 开发期直连哈文 API
      '/api': 'http://127.0.0.1:7000',
      '/static': 'http://127.0.0.1:7000',
    },
  },
})
