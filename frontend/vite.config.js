import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    host: true,
    // vk-tunnel (*.vk-apps.com) сломан на стороне VK с окт. 2025 без ETA, cloudflared режется DPI —
    // рабочий вариант: localhost.run (ssh -R 80:localhost:5173 nokey@localhost.run), выдаёт *.lhr.life
    allowedHosts: ['.vk-apps.com', '.trycloudflare.com', '.lhr.life'],
  },
})
