import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    host: true,
    // vk-tunnel проксирует наружу через *.vk-apps.com — иначе Vite блокирует запрос с незнакомого хоста
    allowedHosts: ['.vk-apps.com'],
  },
})
