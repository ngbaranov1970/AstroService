<script setup>
// Счётчик шагов из макета: римские цифры через точку, активная — золотом.
// Показывает его онбординг над заголовком шага. Принимает номер текущего
// шага и их общее число.

import { computed } from 'vue'

const props = defineProps({
  // Номер текущего шага, считая с единицы: так же, как он выглядит на экране
  current: { type: Number, required: true },
  total: { type: Number, default: 4 },
})

// Римские до шести: больше шагов в мастере не будет, а полноценный
// конвертер ради четырёх значений — лишний код
const ROMAN = ['I', 'II', 'III', 'IV', 'V', 'VI']

// Array.from({length: n}, (_, i) => …) — способ получить список из n
// элементов, аналог range(n) в Python: первый аргумент задаёт длину,
// второй — чем заполнить (i — индекс с нуля).
// _ на месте первого параметра — общая договорённость «значение не нужно»
const steps = computed(() =>
  Array.from({ length: props.total }, (_, index) => ({
    number: index + 1,
    label: ROMAN[index] ?? String(index + 1),
  })),
)
</script>

<template>
  <!-- Точки-разделители — часть оформления, а не текста: рисуем их между
       цифрами, поэтому первую пропускаем через v-if по индексу -->
  <p class="flex items-center justify-center gap-2 font-sans text-label tracking-label">
    <template v-for="(step, index) in steps" :key="step.number">
      <span v-if="index > 0" class="text-gold-dim/50">·</span>
      <!-- Пройденные и будущие шаги выглядят одинаково: макет отличает
           только текущий -->
      <span :class="step.number === current ? 'text-gold' : 'text-gold-dim/60'">
        {{ step.label }}
      </span>
    </template>
  </p>
</template>
