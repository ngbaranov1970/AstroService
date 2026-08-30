<script setup>
// Нижняя навигация: четыре подписи (Главная / Карта / Пара / Профиль).
// Показывает её App.vue на всех экранах, кроме онбординга.
// Данных снаружи не принимает: адрес берёт из роутера, токен карты — из useCharts.

import { computed } from 'vue'
import { useRoute } from 'vue-router'

import { useCharts } from '@/shared/composables/useCharts'

// useRoute() — текущий адрес: имя роута и параметры из шаблона (:token, :feature)
const route = useRoute()
// current — карта, выбранная в переключателе на главной (по умолчанию самая
// свежая); из неё берём token для /chart/:token
const { current } = useCharts()

// Список пунктов пересчитывается сам при смене адреса или карты: computed —
// это значение-формула, Vue помнит зависимости и обновляет только при их
// изменении (в Python ближе всего @property, но с кешем)
const items = computed(() => {
  const name = route.name
  const feature = route.params.feature

  return [
    {
      key: 'home',
      label: 'Главная',
      to: { name: 'home' },
      // Разбор открывается с главной и своего пункта не имеет — подсвечиваем её
      active: name === 'home' || name === 'report',
    },
    {
      key: 'chart',
      label: 'Карта',
      // Карты может не быть (список ещё грузится) — тогда пункт без адреса:
      // ссылка на /chart/undefined увела бы в никуда.
      // ? : — короткое ветвление прямо в выражении, как a if cond else b
      to: current.value ? { name: 'chart', params: { token: current.value.token } } : null,
      active: name === 'chart',
    },
    {
      key: 'pair',
      label: 'Пара',
      to: { name: 'soon', params: { feature: 'pair' } },
      active: name === 'soon' && feature === 'pair',
    },
    {
      key: 'profile',
      label: 'Профиль',
      to: { name: 'soon', params: { feature: 'profile' } },
      active: name === 'soon' && feature === 'profile',
    },
  ]
})
</script>

<template>
  <!-- fixed + left-1/2 + -translate-x-1/2 — прибиваем панель к низу окна и
       центрируем по горизонтали; max-w-app держит её в ширине колонки, иначе
       на десктопе VK она растянулась бы на весь iframe.
       pb-[env(...)] — отступ под «домашнюю» полосу iPhone: без него нижний
       ряд подписей оказывается под системным индикатором -->
  <nav
    class="fixed bottom-0 left-1/2 z-10 w-full max-w-app -translate-x-1/2 border-t border-line-dim bg-bg pb-[env(safe-area-inset-bottom)]"
  >
    <ul class="flex">
      <!-- Каждый пункт занимает равную долю ширины: flex-1 на элементе списка -->
      <li v-for="item in items" :key="item.key" class="flex-1">
        <!-- RouterLink — ссылка, которая меняет адрес внутри приложения, не
             перезагружая страницу. :to принимает не строку, а описание роута
             ({name, params}) — адрес собирает сам роутер по таблице.
             :class с объектом — «класс включён, если значение истинно» -->
        <RouterLink
          v-if="item.to"
          :to="item.to"
          class="block py-4 text-center font-sans text-label tracking-label uppercase transition-colors"
          :class="item.active ? 'text-gold' : 'text-ink-muted'"
        >
          {{ item.label }}
        </RouterLink>

        <!-- Пункт без адреса: та же надпись, но не кликается и приглушена -->
        <span
          v-else
          class="block py-4 text-center font-sans text-label tracking-label text-ink-muted/40 uppercase"
        >
          {{ item.label }}
        </span>
      </li>
    </ul>
  </nav>
</template>
