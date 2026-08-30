<script setup>
// Ряд бейджей главной: Солнце, Луна, Асцендент — три знака зодиака из расчёта.
// Показывает его HomeView, данные передаёт оттуда же (calc.natal карты).
// Своих запросов не делает: пока расчёта нет, рисует те же ячейки с прочерками.

import { computed } from 'vue'

// Внутренние имена точек карты, как их отдаёт backend/astro_core/natal.py
const SUN = 'Sun'
const MOON = 'Moon'

const props = defineProps({
  // calc.natal целиком или null, пока карта не загружена
  natal: { type: Object, default: null },
  // false — время рождения неизвестно, карта посчитана от полудня
  timeKnown: { type: Boolean, default: true },
})

/** Знак планеты по внутреннему имени или null, если расчёта ещё нет. */
function signOf(name) {
  // ?. на всём пути: любой отсутствующий кусок даёт undefined, а не падение —
  // natal может быть null, planets может не оказаться в чужом ответе
  return props.natal?.planets?.find((planet) => planet.name === name)?.sign_ru ?? null
}

// Три ячейки пересчитываются сами, когда карта догрузится
const badges = computed(() => [
  { key: 'sun', label: 'Солнце', value: signOf(SUN) },
  { key: 'moon', label: 'Луна', value: signOf(MOON) },
  {
    key: 'asc',
    label: 'Асцендент',
    // Асцендент сдвигается на знак примерно каждые два часа: без точного
    // времени он посчитан от полудня и означал бы чужую карту. Показываем
    // причину, а не число
    value: props.timeKnown ? (props.natal?.angles?.asc?.sign_ru ?? null) : 'уточните время',
    // Подсказка мельче знака: это не данные, а объяснение, почему их нет
    muted: !props.timeKnown,
  },
])
</script>

<template>
  <!-- grid-cols-3 — три равные колонки; gap задаёт расстояние между ячейками.
       В отличие от flex, ширина здесь одинаковая независимо от длины слова,
       и «Скорпион» не растягивает свою ячейку за счёт соседних -->
  <ul class="grid grid-cols-3 gap-2">
    <li
      v-for="badge in badges"
      :key="badge.key"
      class="rounded-card border border-line-dim bg-bg-card px-2 py-3 text-center"
    >
      <p class="font-sans text-label tracking-label text-ink-muted uppercase">{{ badge.label }}</p>

      <!-- Знак: золотом засечковым. Пока расчёта нет — прочерк того же кегля,
           чтобы высота ряда не менялась после загрузки -->
      <p
        class="mt-1.5 font-display text-gold"
        :class="badge.muted ? 'text-meta text-ink-muted' : 'text-lead'"
      >
        {{ badge.value ?? '—' }}
      </p>
    </li>
  </ul>
</template>
