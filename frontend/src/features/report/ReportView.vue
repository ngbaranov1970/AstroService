<script setup>
// Разбор: один экран на все типы, вид зависит от status из API (available,
// running, ready, locked, failed). Показывает роутер по адресу
// /report/:token/:type. Пока заглушка — вёрстка на шагах 5–6 плана.

import { useRoute } from 'vue-router'

const route = useRoute()
</script>

<template>
  <section class="flex flex-1 flex-col items-center justify-center gap-3 text-center">
    <p class="font-sans text-label tracking-label text-gold-dim uppercase">Шаги 5–6 плана</p>
    <h1 class="font-display text-display text-ink">Разбор</h1>
    <p class="text-meta text-ink-muted">{{ route.params.type }} · {{ route.params.token }}</p>
  </section>
</template>
