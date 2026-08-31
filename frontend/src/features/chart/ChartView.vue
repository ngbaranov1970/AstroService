<script setup>
// Карта: данные рождения и таблицы расчёта — планеты, углы с домами, аспекты.
// Показывает его роутер по адресу /chart/:token, туда же ведёт пункт «Карта»
// в нижней навигации. Токен приезжает в адресе, всё остальное — из useCharts.
//
// Своего запроса нет: ensureDetail держит общий кеш карт с расчётом, и с
// главной сюда приходят с уже готовыми данными.

import { computed, watch } from 'vue'
import { useRoute } from 'vue-router'

import { useCharts } from '@/shared/composables/useCharts'
import AppButton from '@/shared/ui/AppButton.vue'

import { aspectRows, houseRows, planetRows } from './chartRows'
import ChartTable from './ChartTable.vue'

const route = useRoute()
const { detail, ensureDetail } = useCharts()

// Параметры роутера бывают и массивом (таков его контракт) — приводим к строке
const token = computed(() => String(route.params.token ?? ''))

// Состояние одной карты: {status, error, chart}. Про незнакомый токен вернётся
// idle — «не спрашивали», а не «нет такой»
const state = computed(() => detail(token.value))
const chart = computed(() => state.value.chart)
const natal = computed(() => chart.value?.calc?.natal ?? null)

// Время рождения известно — только тогда дома и куспиды означают эту карту,
// а не полдень. Пока карта не пришла, считаем, что известно: так ветка
// «домов нет» не мелькнёт на кадр перед появлением таблиц
const timeKnown = computed(() => chart.value?.time_known ?? true)

// Три набора строк для одинаковых таблиц. Пересчитываются сами, когда карта
// догрузится: пока natal null, все три пустые
const planets = computed(() => planetRows(natal.value, timeKnown.value))
const houses = computed(() => (timeKnown.value ? houseRows(natal.value) : []))
const aspects = computed(() => aspectRows(natal.value))

// Заголовок экрана: имя из карты, а безымянная карта всё равно чья-то
const name = computed(() => chart.value?.person_name || 'Твоя карта')

// Подпись под именем: дата рождения, время (если известно) и место
const birthLine = computed(() => {
  if (!chart.value) return ''

  const born = new Date(chart.value.birth_local)

  // Intl.DateTimeFormat печатает дату по-русски со склонением месяца; ту же
  // пару new Date + Intl использует переключатель карт на главной
  const date = new Intl.DateTimeFormat('ru-RU', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  }).format(born)

  const time = timeKnown.value
    ? new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' }).format(born)
    : 'время неизвестно'

  // filter(Boolean) выбрасывает пустое место: карту заводят и без города
  return [date, time, chart.value.place_name].filter(Boolean).join(' · ')
})

// Идёт первая загрузка: карты ещё нет и её сейчас везут
const isLoading = computed(() => !chart.value && state.value.status !== 'error')

/** Забрать карту заново — после обрыва. ensureDetail при статусе error
 *  запускает загрузку сам: готовым он считает только ready. */
function retry() {
  return ensureDetail(token.value)
}

// Заход по прямой ссылке — единственный случай, когда карты в кеше нет:
// с главной сюда приходят с готовыми данными. ensureDetail от повторов защищён
watch(
  token,
  (value) => {
    if (value) ensureDetail(value)
  },
  { immediate: true },
)
</script>

<template>
  <div class="flex flex-1 flex-col">
    <!-- Шапка одна на все состояния: чью карту открыли и по каким данным она
         посчитана. Пока карта едет, строки просто пустые -->
    <header class="mb-7 min-w-0">
      <p class="font-sans text-label tracking-label text-gold-dim uppercase">Натальная карта</p>
      <h1 class="mt-2 truncate font-display text-display text-ink">{{ name }}</h1>
      <p v-if="birthLine" class="mt-1 font-sans text-meta text-ink-muted">{{ birthLine }}</p>
    </header>

    <!-- 1. Карту везут. Пустых таблиц не рисуем: строки появятся через миг,
         а «карта без единой планеты» на секунду выглядит поломкой расчёта -->
    <section v-if="isLoading" class="flex flex-1 items-center justify-center">
      <p class="animate-pulse font-display text-title text-ink-muted">Открываем карту…</p>
    </section>

    <!-- 2. Не доехала: сеть или сервер. Единственное осмысленное действие —
         попробовать ещё раз -->
    <section
      v-else-if="!chart"
      class="flex flex-1 flex-col items-center justify-center gap-4 text-center"
    >
      <h1 class="font-display text-title text-ink">Не удалось открыть карту</h1>
      <p class="font-sans text-meta text-ink-muted">{{ state.error }}</p>
      <div class="mt-2 w-full">
        <AppButton @click="retry">Повторить</AppButton>
      </div>
    </section>

    <!-- 3. Карта есть, а расчёта в ней нет. Так бывает у старых записей и
         случится, если ядро сменит формат calc: показываем причину, а не
         падаем на пустых таблицах (docs/frontend-plan.md, «Риски») -->
    <section
      v-else-if="!natal"
      class="flex flex-1 flex-col items-center justify-center gap-4 text-center"
    >
      <h1 class="font-display text-title text-ink">Расчёт недоступен</h1>
      <p class="font-read text-lead text-ink-muted">
        Данные этой карты не удалось прочитать. Попробуй завести её заново.
      </p>
    </section>

    <!-- 4. Ради чего экран и сделан -->
    <div v-else class="space-y-7">
      <ChartTable title="Планеты" :rows="planets" />

      <ChartTable v-if="timeKnown" title="Углы и дома" :rows="houses" />

      <!-- Без точного времени куспидов нет вовсе: показывать посчитанные от
           полудня — врать. Планеты и аспекты от времени почти не зависят -->
      <p v-else class="font-read text-lead text-ink-muted">
        Дома и Асцендент считаются от точного времени рождения — без него их не
        показываем.
      </p>

      <ChartTable title="Аспекты" :rows="aspects" />

      <!-- Условия расчёта: по ним карту сверяют с любым другим калькулятором,
           и разночтение чаще всего именно в системе домов или зоне -->
      <p class="font-sans text-meta text-ink-muted">
        Дома по Placidus, зодиак тропический. Часовой пояс {{ natal.meta?.tz ?? '—' }}.
      </p>
    </div>
  </div>
</template>
