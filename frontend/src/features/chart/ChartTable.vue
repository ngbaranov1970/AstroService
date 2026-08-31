<script setup>
// Одна секция таблицы карты: заголовок и строки «имя — значение — приписка».
// Показывает её ChartView трижды (планеты, дома, аспекты), строки готовит
// chartRows.js. Своих данных и запросов нет — чистый показ.
//
// Один компонент на три таблицы вместо трёх похожих: колонки у них одинаковые,
// а правка вёрстки в трёх местах разъезжается на второй же итерации.

// defineProps объявляет, что компонент принимает снаружи. Это макрос
// компилятора Vue, импортировать его не нужно — он доступен в <script setup>
defineProps({
  // Заголовок секции: «Планеты», «Углы и дома», «Аспекты»
  title: { type: String, required: true },
  // Строки: [{key, name, value, note, dim}] — их собирает chartRows.js.
  // default у объектов и массивов задаётся функцией: иначе один и тот же
  // массив стал бы общим для всех экземпляров компонента (та же грабля,
  // что у изменяемого значения по умолчанию в Python)
  rows: { type: Array, default: () => [] },
})
</script>

<template>
  <section class="space-y-3">
    <h2 class="font-sans text-label tracking-label text-gold-dim uppercase">{{ title }}</h2>

    <!-- divide-y — линия между соседними элементами списка (по краям её нет).
         Рамка вокруг всей таблицы не нужна: строки и так читаются рядами -->
    <ul class="divide-y divide-line-dim border-y border-line-dim">
      <!-- items-baseline ровняет по нижней линии букв: кегли в строке разные,
           и выравнивание по краю блока заметно «прыгало» бы -->
      <li
        v-for="row in rows"
        :key="row.key"
        class="flex items-baseline justify-between gap-3 py-2.5"
      >
        <!-- min-w-0 разрешает длинному имени переноситься, а не растягивать
             строку: без него flex-элемент не сжимается меньше своего текста -->
        <span
          class="min-w-0 font-display text-lead"
          :class="row.dim ? 'text-ink-muted' : 'text-ink'"
        >
          {{ row.name }}
        </span>

        <!-- Значение и приписка держатся вместе справа. shrink-0 не даёт их
             сжать: перенос «11°07′ Лев» посреди числа выглядит поломкой -->
        <span class="flex shrink-0 items-baseline gap-2">
          <span class="font-display text-lead text-gold">{{ row.value }}</span>
          <!-- Ширина колонки приписок фиксирована, поэтому «R 3 дом» и пустое
               место не сдвигают значения соседних строк -->
          <span class="w-14 text-right font-sans text-meta text-ink-muted">{{ row.note }}</span>
        </span>
      </li>
    </ul>
  </section>
</template>
