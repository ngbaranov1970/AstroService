<script setup>
// «Скоро»: общий экран-заглушка для неготовых разделов (Пара, Матрица Судьбы,
// восточный гороскоп). Показывает роутер по адресу /soon/:feature — какой
// именно раздел, сказано в адресе.

import { useRoute } from 'vue-router'

const route = useRoute()

// Подписи разделов держим здесь: экран один на всех, а название нужно своё.
// Обычный объект в роли словаря — как dict в Python
const TITLES = {
  pair: 'Совместимость',
  matrix: 'Матрица Судьбы',
  eastern: 'Восточный гороскоп',
}
</script>

<template>
  <section class="flex flex-1 flex-col items-center justify-center gap-3 text-center">
    <h1 class="font-display text-display text-ink">
      {{ TITLES[route.params.feature] ?? 'Скоро' }}
    </h1>
    <p class="font-display text-lead text-ink-muted italic">Этот раздел ещё готовится</p>
  </section>
</template>
