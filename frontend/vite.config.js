import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath, URL } from 'node:url'

// https://vite.dev/config/
export default defineConfig({
  // vue() — компилирует .vue в JS, tailwindcss() — собирает CSS по классам в шаблонах
  plugins: [vue(), tailwindcss()],
  resolve: {
    alias: {
      // '@' = папка src: импорты не превращаются в '../../../shared/api/http'
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  // Собранный фронт живёт по адресу ngbaranov.top/app/ — ссылки на js и css
  // Vite проставляет на этапе сборки, из браузера этот путь уже не поменять
  base: '/app/',
  server: {
    host: true,
    // vk-tunnel (*.vk-apps.com) сломан на стороне VK с окт. 2025 без ETA, cloudflared режется DPI —
    // рабочий вариант: localhost.run (ssh -R 80:localhost:5173 nokey@localhost.run), выдаёт *.lhr.life
    allowedHosts: ['.vk-apps.com', '.trycloudflare.com', '.lhr.life'],
    proxy: {
      // Браузер видит запрос на свой же адрес, dev-сервер молча пересылает его
      // в FastAPI — CORS в бэкенде не нужен ни в разработке, ни на проде
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
