import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
      '@/components': path.resolve(__dirname, './src/components'),
      '@/features': path.resolve(__dirname, './src/features'),
      '@/hooks': path.resolve(__dirname, './src/hooks'),
      '@/lib': path.resolve(__dirname, './src/lib'),
      '@/store': path.resolve(__dirname, './src/store'),
      '@/types': path.resolve(__dirname, './src/types'),
    },
  },
  server: {
    port: 4000,
    proxy: {
      // Dev 下把相对路径 /api/* 转发到后端 FastAPI，让 EventSource('/api/videos/pipeline/{id}/progress')
      // 这种相对路径的 SSE 也能直连后端。target 与 .env 的 VITE_API_BASE 同源，避免 CORS。
      '/api': {
        target: 'http://localhost:8002',
        changeOrigin: true,
      },
    },
  },
})