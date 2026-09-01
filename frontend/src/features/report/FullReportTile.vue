<script setup>
// Плитка «Полный разбор» в конце бесплатного разбора: название, состав из
// шести секций и один переход. Показывает её ReportView на экране free_natal.
// Принимает token карты и запись разбора full_natal из useReports.
//
// Внутри VK нет ни цены, ни слов «купить» и «оплатить»: продавать в Mini App
// правилами VK запрещено, поэтому отсюда только уводим на сайт, а сумма и
// оплата живут там (docs/astro-context.md).
//
// В обычном браузере запрета нет, и оплата идёт внутри приложения: плитка
// ведёт на свой экран /pay/:token, а наружу уходит только форма ЮKassa.

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

// Домен сайта, куда уходит оплата. Боевой адрес — дефолт: на машине сборки
// переменной может не быть, и тогда ссылка обязана вести на прод.
// import.meta.env — переменные окружения сборки (аналог os.environ, только
// значения подставляются в код на этапе сборки, а не читаются в рантайме);
// наружу Vite отдаёт лишь те, что начинаются с VITE_.
// В разработке .env.development подменяет адрес на локальный: карта заведена
// локальным бэком, и на проде её токен неизвестен — страница оплаты отвечает
// 404 «Такой карты нет»
const SITE_URL = import.meta.env.VITE_SITE_URL ?? 'https://astrozvezdochet.ru'

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

// Единственное состояние, ведущее к оплате. Всё остальное — «разбор уже наш»,
// и открывается он внутри приложения
const isLocked = computed(() => props.report?.status === REPORT_STATUS.LOCKED)

// В обычном браузере оплата целиком наша: и витрина, и возврат — экраны Vue.
// В VK и Telegram платить уходят во внешний браузер, на страницы сайта
const isWeb = computed(() => platform.name === 'web')

// Цену сервер присылает всем, кроме пришедших из VK (backend api/routes/reports.py)
const price = computed(() => props.report?.price_rub ?? null)

// Название берём с сервера: тем же именем товар назван на странице оплаты
const title = computed(() => props.report?.title ?? 'Полный разбор натальной карты')

// Состав — анонс до генерации: шесть заголовков секций из промпта
const sections = computed(() => props.report?.section_titles ?? [])

const action = computed(() => {
  if (isLocked.value) {
    // В VK говорим только про переход: ни цены, ни слова «оформить».
    // Где запрет не действует, называем вещи своими именами и показываем сумму
    if (platform.name === 'vk') return 'Открыть'
    return price.value === null ? 'Оформить' : `Оформить за ${price.value} ₽`
  }

  return ACTION[props.report?.status] ?? 'Открыть'
})

function open() {
  if (isLocked.value && !isWeb.value) {
    // Внешний браузер, а не переход внутри приложения: цифровые товары внутри
    // Mini App разрешены только за «голоса», а на iOS запрещены вовсе.
    // back=vk просит страницу «спасибо» дать ссылку обратно в приложение —
    // сама она этого не угадает, платят из чужого браузера
    const back = platform.name === 'vk' ? '?back=vk' : ''
    platform.openExternal(`${SITE_URL}/pay/${props.token}${back}`)
    return
  }

  // push — переход на другой экран приложения, как клик по RouterLink.
  // Заперт и мы в браузере — ведём на свою витрину, иначе сразу в разбор
  router.push(
    isLocked.value
      ? { name: 'pay', params: { token: props.token } }
      : { name: 'report', params: { token: props.token, type: 'full_natal' } },
  )
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
