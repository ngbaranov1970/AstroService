<script setup>
// Карта: таблицы планет, домов и аспектов из calc.natal. Показывает роутер
// по адресу /chart/:token — токен карты приезжает в адресе.
// Пока заглушка — вёрстка на шаге 7 плана.

import { useRoute } from 'vue-router'

// useRoute() отдаёт текущий адрес: params — то, что стоит в шаблоне роута
// на месте :token. Это ref-подобный объект, .value писать не нужно
const route = useRoute()
</script>

<template>
  <section class="flex flex-1 flex-col items-center justify-center gap-3 text-center">
    <p class="font-sans text-label tracking-label text-gold-dim uppercase">Шаг 7 плана</p>
    <h1 class="font-display text-display text-ink">Карта</h1>
    <p class="text-meta text-ink-muted">token: {{ route.params.token }}</p>
  </section>
</template>
