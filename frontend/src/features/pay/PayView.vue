<script setup>
// Витрина оплаты внутри приложения: что покупается, за сколько и кнопка на
// форму ЮKassa. Показывает её роутер по адресу /pay/:token, приводит сюда
// плитка «Полный разбор» на веб-платформе.
//
// В VK этого экрана нет и быть не может: продавать внутри Mini App запрещено
// (docs/astro-context.md), поэтому пришедшего оттуда сразу уводим обратно.
// API на той стороне отвечает VK-пользователю 403 — то же правило, но
// проверенное сервером.

import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { platform } from '@/app/platform'
import { createOrder, createPairOrder, REPORT_STATUS } from '@/shared/api/endpoints'
import { useCharts } from '@/shared/composables/useCharts'
import { usePairs } from '@/shared/composables/usePairs'
import { useReports } from '@/shared/composables/useReports'
import { openLegal } from '@/shared/legal'
import AppButton from '@/shared/ui/AppButton.vue'

// Что покупается на этом адресе: разбор карты или разбор пары. Маршрут
// построен на владельце, а не на типе — типов у одной карты со временем
// станет несколько, и менять придётся эту таблицу, а не адрес
const PAID_TYPE = Object.freeze({ chart: 'full_natal', pair: 'pair_full' })

const route = useRoute()
const router = useRouter()
// Чей это товар, говорит сам маршрут (meta.scope) — как на экране разбора
const scope = computed(() => route.meta.scope ?? 'chart')
const isPair = computed(() => scope.value === 'pair')
const paidType = computed(() => PAID_TYPE[scope.value])

// useReports зовём внутри computed: Vue Router переиспользует компонент между
// маршрутами, у которых он общий, и замыкание ходило бы в прежние ручки
const api = computed(() => useReports(scope.value))
const { charts } = useCharts()
const { detail: pairDetail } = usePairs()

const token = computed(() => String(route.params.token ?? ''))

// Чья карта покупается. Берём из общего списка, а не отдельным запросом:
// расчёт витрине не нужен, хватит имени, даты и места. У пары карты нет —
// там подпись собирается ниже из имён обеих сторон
const chart = computed(() =>
  isPair.value ? null : (charts.value.find((item) => item.token === token.value) ?? null),
)

// Имена сторон пары. Берём из уже загруженной пары и отдельного запроса не
// делаем: сюда приходят с экрана пары, где она загружена. Не доехала —
// обойдёмся названием товара, оно и так называет покупку
const pairLine = computed(() => {
  const pair = isPair.value ? pairDetail(token.value).pair : null
  if (!pair) return ''

  return `${pair.person_a || 'Ты'} и ${pair.person_b || 'Партнёр'}`
})

// Подпись под названием товара: та же строка, что на экране карты. Человек
// должен видеть, за чей разбор платит, — карт у него может быть несколько
const birthLine = computed(() => {
  if (!chart.value) return ''

  const born = new Date(chart.value.birth_local)
  // Intl.DateTimeFormat печатает дату по-русски со склонением месяца
  const date = new Intl.DateTimeFormat('ru-RU', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  }).format(born)
  const time = chart.value.time_known
    ? new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' }).format(born)
    : 'время неизвестно'

  // filter(Boolean) выбрасывает пустое место: карту заводят и без города
  return [date, time, chart.value.place_name].filter(Boolean).join(' · ')
})

// Тот же список разборов, что на экране чтения: цена и состав приходят в нём
const current = computed(() => api.value.report(token.value, paidType.value))
const listStatus = computed(() => api.value.state(token.value).status)

const title = computed(
  () =>
    current.value?.title ??
    (isPair.value ? 'Полный разбор совместимости' : 'Полный разбор натальной карты'),
)
const sections = computed(() => current.value?.section_titles ?? [])
// null означает «покупать нельзя»: сервер не прислал цену (мы в VK или разбор
// не продаётся). Кнопки в этом случае не будет
const price = computed(() => current.value?.price_rub ?? null)

// Список ещё едет — на экране ожидание, а не «разбор недоступен»
const isLoading = computed(() => !current.value && listStatus.value !== 'error')

// Имена маршрутов разбора: у пары свой, но экран за ними один и тот же
const reportRoute = computed(() => (isPair.value ? 'pair-report' : 'report'))

/** Уйти читать разбор. replace, а не push: витрина уже неактуальна, и
 *  «назад» с экрана разбора не должно возвращать на неё. */
function goToReport() {
  router.replace({
    name: reportRoute.value,
    params: { token: token.value, type: paidType.value },
  })
}

// Куда ведёт «Назад»: к бесплатному разбору, из которого сюда и приходят
const backTo = computed(() => ({
  name: reportRoute.value,
  params: { token: token.value, type: isPair.value ? 'pair_free' : 'free_natal' },
}))

// Своё состояние кнопки: ошибку заказа ждёт человек, который только что нажал
const starting = ref(false)
const error = ref(null)

/** Завести заказ и уйти на форму оплаты. */
async function pay() {
  starting.value = true
  error.value = null

  try {
    // Ручки заказа у карты и пары разные: у пары свой замок против двух
    // ожидающих заказов и своя цена
    const order = isPair.value
      ? await createPairOrder(token.value)
      : await createOrder(token.value)
    // Переход в ЭТОЙ вкладке, а не window.open: оплата не должна выглядеть
    // всплывшим окном чужого сайта, а вернёт нас ЮKassa сама — на адрес
    // возврата, который сервер построил при создании заказа.
    // location.assign — переход браузера по адресу, минуя роутер
    window.location.assign(order.confirmation_url)
    // starting не снимаем: страница уже уезжает, и кнопка должна остаться
    // заблокированной, чтобы по ней не нажали второй раз
  } catch (failure) {
    // 409 — разбор уже оплачен (заплатили с другого устройства, вернулись по
    // старой ссылке). Платить второй раз не за что, ведём читать
    if (failure.status === 409) {
      goToReport()
      return
    }
    error.value = 'Не получилось перейти к оплате. Попробуй ещё раз через минуту.'
    starting.value = false
  }
}

// Список разборов у карты общий на приложение, но сюда могут зайти по прямой
// ссылке — тогда его ещё никто не грузил
watch(
  token,
  (value) => {
    if (value) api.value.ensureLoaded(value)
  },
  { immediate: true },
)

// Разбор перестал быть запертым (купили, уже пишется, готов) — витрине здесь
// делать нечего. Сработает и сразу после загрузки списка, и при возврате на
// этот адрес «назад» после оплаты
watch(current, (value) => {
  if (value && value.status !== REPORT_STATUS.LOCKED) goToReport()
})

// onMounted — «компонент появился на экране» (аналога в Python нет, это
// событие жизненного цикла). Проверку платформы делаем здесь, а не в setup:
// переход внутри роутера должен начинаться после того, как текущий закончен
onMounted(() => {
  if (platform.name === 'vk') goToReport()
})
</script>

<template>
  <div class="flex flex-1 flex-col">
    <!-- Шапка как на экране разбора: где мы и куда уйти назад -->
    <header class="mb-7 flex items-baseline justify-between gap-3">
      <p class="min-w-0 truncate font-sans text-label tracking-label text-gold-dim uppercase">
        Оформление
      </p>
      <RouterLink
        :to="backTo"
        class="shrink-0 font-sans text-label tracking-label text-gold-dim uppercase"
      >
        ← Назад
      </RouterLink>
    </header>

    <!-- 1. Список разборов ещё едет: цену и состав брать неоткуда -->
    <section
      v-if="isLoading"
      class="flex flex-1 flex-col items-center justify-center text-center"
    >
      <p class="animate-pulse font-display text-title text-ink-muted">Открываем…</p>
    </section>

    <!-- 2. Не доехал: без цены витрину рисовать нельзя -->
    <section
      v-else-if="!current"
      class="flex flex-1 flex-col items-center justify-center gap-4 text-center"
    >
      <h1 class="font-display text-title text-ink">Не удалось открыть оплату</h1>
      <div class="mt-2 w-full">
        <AppButton @click="api.reload(token)">Повторить</AppButton>
      </div>
    </section>

    <!-- 3. Витрина: товар, состав, цена, кнопка -->
    <section v-else class="flex flex-1 flex-col">
      <h1 class="font-display text-display text-ink">{{ title }}</h1>
      <p v-if="birthLine" class="mt-2 font-sans text-meta text-ink-muted">
        <!-- Имя показываем, только если оно есть: карту заводят и безымянной -->
        <template v-if="chart?.person_name">{{ chart.person_name }} · </template>{{ birthLine }}
      </p>

      <!-- У пары вместо данных рождения — имена сторон: пар у человека может
           быть несколько, и он должен видеть, за какую платит. Дат и мест
           здесь нет: за парой стоят двое, и второй согласия не давал -->
      <p v-else-if="pairLine" class="mt-2 font-sans text-meta text-ink-muted">{{ pairLine }}</p>

      <!-- Состав будущего текста: шесть заголовков секций из промпта -->
      <ul v-if="sections.length" class="mt-7 space-y-2">
        <li
          v-for="name in sections"
          :key="name"
          class="border-b border-line-dim pb-2 font-display text-lead text-gold"
        >
          {{ name }}
        </li>
      </ul>

      <!-- mt-auto прижимает цену и кнопку к низу экрана: между составом и
           ними остаётся воздух, сколько бы секций ни пришло -->
      <div class="mt-auto pt-10">
        <p v-if="price !== null" class="text-center font-display text-display text-ink">
          {{ price }} ₽
        </p>

        <div v-if="price !== null" class="mt-5">
          <AppButton :loading="starting" loading-text="Открываем оплату…" @click="pay">
            Перейти к оплате
          </AppButton>
        </div>

        <!-- Цены нет — покупать нечем. Сюда попадают, только если разбор
             сняли с продажи, пока экран был открыт -->
        <p v-else class="text-center font-read text-lead text-ink-muted">
          Этот разбор сейчас не продаётся.
        </p>

        <p v-if="error" class="mt-3 text-center font-sans text-meta text-ink-muted">
          {{ error }}
        </p>

        <!-- Юридическая часть: без согласия с офертой кнопку нажимать нельзя,
             а чек самозанятого выписывается вручную и письмо ЮKassa чеком не
             является (решение 2026-08-13). Оферта и политика — страницы сайта,
             а не экраны приложения, поэтому не RouterLink и не голая ссылка:
             openLegal уводит их наружу через адаптер платформы -->
        <p class="mt-5 font-sans text-meta text-ink-muted">
          Нажимая кнопку, ты принимаешь условия
          <!-- Кнопки, а не ссылки, но выглядят ссылкой: перенос строки внутри
               тега добавил бы пробел перед точкой, поэтому пишем в одну -->
          <button type="button" class="cursor-pointer text-gold-dim underline" @click="openLegal('/offer')">оферты</button>
          и соглашаешься с
          <button type="button" class="cursor-pointer text-gold-dim underline" @click="openLegal('/privacy')">обработкой данных</button>.
          Оплата проходит на стороне ЮKassa — данные карты нам не передаются.
        </p>
        <p class="mt-2 font-sans text-meta text-ink-muted">
          Разбор откроется сразу после оплаты. Чек самозанятого придёт отдельно.
        </p>
      </div>
    </section>
  </div>
</template>
