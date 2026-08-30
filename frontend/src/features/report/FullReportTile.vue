<script setup>
// Плитка «Полный разбор» в конце бесплатного разбора: название, состав из
// шести секций и один переход. Показывает её ReportView на экране free_natal.
// Принимает token карты и запись разбора full_natal из useReports.
//
// Внутри VK нет ни цены, ни слов «купить» и «оплатить»: продавать в Mini App
// правилами VK запрещено, поэтому отсюда только уводим на сайт, а сумма и
// оплата живут там (docs/astro-context.md).

import { computed } from 'vue'
import { useRouter } from 'vue-router'

import { platform } from '@/app/platform'
import { REPORT_STATUS } from '@/shared/api/endpoints'
import ReportIcon from '@/shared/ui/ReportIcon.vue'

const props = defineProps({
  token: { type: String, required: true },
  // null — список разборов ещё не пришёл: плитка рисуется по запасным текстам,
  // а переход работает и без него, token для ссылки у нас уже есть
  report: { type: Object, default: null },
})

// Домен сайта, куда уходит оплата. Константой, а не переменной окружения:
// адрес канонический и один на весь проект (он же в кабинетах ЮKassa и VK),
// а .env пришлось бы держать на каждой машине сборки — он в .gitignore
const SITE_URL = 'https://astrozvezdochet.ru'

// useRouter даёт объект навигации (перейти на другой экран), в отличие от
// useRoute, который отдаёт текущий адрес
const router = useRouter()

// Подпись перехода на каждое состояние разбора, кроме locked: у того текст
// зависит ещё и от платформы, он ниже
const ACTION = Object.freeze({
  [REPORT_STATUS.READY]: 'Читать',
  [REPORT_STATUS.RUNNING]: 'Готовится',
  [REPORT_STATUS.AVAILABLE]: 'Получить разбор',
  [REPORT_STATUS.FAILED]: 'Попробовать снова',
})

// Единственное состояние, из которого уходим на сайт. Всё остальное —
// «разбор уже наш», и открывается он внутри приложения
const isLocked = computed(() => props.report?.status === REPORT_STATUS.LOCKED)

// Название берём с сервера: тем же именем товар назван на странице оплаты
const title = computed(() => props.report?.title ?? 'Полный разбор натальной карты')

// Состав — анонс до генерации: шесть заголовков секций из промпта
const sections = computed(() => props.report?.section_titles ?? [])

const action = computed(() => {
  // В VK говорим только про переход. На web и в Telegram платформа фронту
  // известна и запрет не действует — там можно назвать вещи своими именами
  if (isLocked.value) return platform.name === 'vk' ? 'Открыть' : 'Оформить на сайте'

  return ACTION[props.report?.status] ?? 'Открыть'
})

function open() {
  if (isLocked.value) {
    // Внешний браузер, а не переход внутри приложения: цифровые товары внутри
    // Mini App разрешены только за «голоса», а на iOS запрещены вовсе
    platform.openExternal(`${SITE_URL}/pay/${props.token}`)
    return
  }

  // push — переход на другой экран приложения, как клик по RouterLink
  router.push({ name: 'report', params: { token: props.token, type: 'full_natal' } })
}
</script>

<template>
  <!-- Корень — <button>, а не ссылка: клик ведёт то наружу, то внутрь, и
       href пришлось бы подменять на лету. Кнопка одинаково ловит фокус с
       клавиатуры, а текст ей выравниваем влево (по умолчанию он по центру).
       Рамка полного золота — на экране разбора это главное действие -->
  <button
    type="button"
    class="w-full rounded-card border border-gold bg-bg-card p-5 text-left transition-colors active:bg-gold/5"
    @click="open"
  >
    <div class="flex items-center gap-4">
      <!-- shrink-0 запрещает flex сплющить иконку под длинным заголовком -->
      <ReportIcon name="natal" class="w-11 shrink-0 text-gold" />
      <p class="min-w-0 font-display text-title text-ink">{{ title }}</p>
    </div>

    <!-- Состав одной строкой через разделитель. join склеивает массив в
         строку — то же, что ' · '.join(...) в Python -->
    <p v-if="sections.length" class="mt-3 font-sans text-meta text-ink-muted">
      {{ sections.join(' · ') }}
    </p>

    <p class="mt-4 font-sans text-label tracking-label text-gold uppercase">{{ action }} →</p>
  </button>
</template>
