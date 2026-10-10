<script setup>
// Блок «Напиши нам» — вход в VIP-разбор через личку сообщества. Стоит там,
// где раньше был запертый полный разбор: на главной и в конце бесплатного.
// Показывают HomeView и ReportView. Принимает place — где стоит блок,
// оно уходит в событие воронки.
//
// Ни цены, ни «купить»: VIP продаётся только в переписке, а в приложении
// продавать запрещено правилами VK (docs/sales-policy.md, «Правила текстов»).

import { platform } from '@/app/platform'
import { trackVipCta } from '@/shared/api/endpoints'
import ReportIcon from '@/shared/ui/ReportIcon.vue'

const props = defineProps({
  // 'home' или 'report' — те же значения принимает сервер, другое он отвергнет
  place: { type: String, required: true },
})

// Личка сообщества. Адрес короткий и постоянный, поэтому константа, а не
// group_id с сервера: тот вне VK приходит null, а блок нужен и в браузере
const MESSAGES_URL = 'https://vk.me/astrozvezdochet'

function open() {
  // Событие отправляем и не ждём ответа: человек пришёл писать, а не смотреть
  // на загрузку. .catch(() => {}) глушит ошибку — потерянное событие хуже
  // для статистики, но не для человека, и пугать его нечем
  trackVipCta(props.place).catch(() => {})
  // Наружу тем же способом, что и оплата: на iOS внутри VK window.open молчит
  platform.openExternal(MESSAGES_URL)
}
</script>

<template>
  <!-- Та же рамка и раскладка, что у FullReportTile: блок занял её место,
       и экран не должен «прыгать» для тех, кто видел старую плитку -->
  <button
    type="button"
    class="w-full rounded-card border border-gold bg-bg-card p-5 text-left transition-colors active:bg-gold/5"
    @click="open"
  >
    <div class="flex items-center gap-4">
      <ReportIcon name="natal" class="w-11 shrink-0 text-gold" />
      <p class="min-w-0 font-display text-title text-ink">Подробный разбор от астролога</p>
    </div>

    <p class="mt-3 font-read text-read text-ink-muted">
      Хочешь подробный разбор, который астролог посмотрит лично? Напиши нам — расскажем, что
      входит.
    </p>

    <p class="mt-4 font-sans text-label tracking-label text-gold uppercase">Напиши нам →</p>
  </button>
</template>
