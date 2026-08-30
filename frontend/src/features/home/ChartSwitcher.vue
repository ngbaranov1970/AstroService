<script setup>
// Имя человека в шапке главной — оно же переключатель карт: тап разворачивает
// список, тап по строке меняет карту, вокруг которой крутится приложение.
// Показывает его HomeView; данные берёт сам из useCharts, снаружи ничего не
// принимает. Заводят новую карту не здесь, а карточкой в списке разборов.
//
// Кликабельно именно имя, а не иконка рядом: круг с буквой ничего не обещает,
// а имя со стрелкой человек узнаёт по любому другому приложению.

import { computed, ref } from 'vue'

import { useCharts } from '@/shared/composables/useCharts'

const { charts, current, select } = useCharts()

// Открыт ли список. false — на экране просто заголовок с именем
const open = ref(false)

const name = computed(() => current.value?.person_name || 'Твоя карта')

// Карта одна — переключать нечего: тогда это обычный заголовок, без стрелки и
// без нажатия. Намёк на список там, где списка нет, только сбивает
const hasChoice = computed(() => charts.value.length > 1)

/** Подпись карты в списке: дата рождения и город. */
function birthLine(chart) {
  // Дату сервер отдаёт строкой вида «1990-11-14T12:30:00»; new Date разбирает
  // её сам, а Intl.DateTimeFormat печатает по-русски (та же пара, что в шапке)
  const date = new Intl.DateTimeFormat('ru-RU', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  }).format(new Date(chart.birth_local))

  return chart.place_name ? `${date} · ${chart.place_name}` : date
}

/** Выбрать карту и закрыть список. Главная перестроится сама: она смотрит на
 *  current, а расчёт и разборы новой карты подтянет её же watch. */
function choose(token) {
  select(token)
  open.value = false
}
</script>

<template>
  <!-- relative — точка отсчёта для списка: он позиционируется absolute и
       встанет по краю этого блока, а не всей страницы -->
  <div class="relative min-w-0">
    <h1 class="min-w-0">
      <!-- Одна карта: обычный заголовок. Кнопка без действия ловила бы фокус
           и обещала то, чего нет -->
      <span v-if="!hasChoice" class="block truncate font-display text-display text-ink">
        {{ name }}
      </span>

      <button
        v-else
        type="button"
        class="flex max-w-full items-center gap-2"
        :aria-expanded="open"
        @click="open = !open"
      >
        <!-- truncate обрезает длинное имя многоточием; min-w-0 разрешает flex
             его сжимать — без этого текст выдавил бы стрелку за край -->
        <span class="min-w-0 truncate font-display text-display text-ink">{{ name }}</span>

        <!-- Стрелка вниз одной линией: рисуем сами, иконочных пакетов в
             проекте нет. При открытом списке переворачиваем — понятно, что
             это одна и та же створка.
             stroke-* — цвет и толщина линии (заливки у иконки нет),
             currentColor берёт цвет текста, заданный классом text-gold -->
        <svg
          viewBox="0 0 20 20"
          fill="none"
          stroke="currentColor"
          stroke-width="1.5"
          class="size-5 shrink-0 text-gold transition-transform"
          :class="open && 'rotate-180'"
          aria-hidden="true"
        >
          <path d="M5 8 L10 13 L15 8" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </button>
    </h1>

    <!-- Тап мимо списка закрывает его. Прозрачная плоскость на весь экран
         вместо слушателя на document: она живёт ровно столько же, сколько
         список, и отписываться от неё не нужно -->
    <div v-if="open" class="fixed inset-0 z-10" @click="open = false" />

    <!-- Список поверх подложки, прижат к левому краю заголовка -->
    <div
      v-if="open"
      class="absolute left-0 z-20 mt-2 w-64 rounded-card border border-line-gold bg-bg-card"
    >
      <button
        v-for="chart in charts"
        :key="chart.token"
        type="button"
        class="block w-full border-b border-line-dim px-4 py-3 text-left transition-colors last:border-b-0 active:bg-gold/5"
        @click="choose(chart.token)"
      >
        <!-- Текущая карта отмечена цветом, а не галочкой: в макете нет ни
             одной иконки-указателя, и золото читается тем же способом -->
        <p
          class="truncate font-display text-lead"
          :class="chart.token === current?.token ? 'text-gold' : 'text-ink'"
        >
          {{ chart.person_name || 'Без имени' }}
        </p>
        <p class="truncate font-sans text-meta text-ink-muted">{{ birthLine(chart) }}</p>
      </button>
    </div>
  </div>
</template>
