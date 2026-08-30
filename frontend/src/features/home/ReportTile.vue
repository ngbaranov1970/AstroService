<script setup>
// Плитка разбора на главной: иконка, заголовок и подпись-статус, всё внутри
// ссылки. Показывает её HomeView в цикле по плиткам из homeTiles.js.
// Принимает одну готовую запись плитки и ничего о разборах не знает: заголовок,
// подпись и адрес посчитаны до неё.

import { computed } from 'vue'

import ReportIcon from '@/shared/ui/ReportIcon.vue'

const props = defineProps({
  // required вместо default: плитка без данных — это ошибка в HomeView, и
  // лучше увидеть предупреждение в консоли, чем пустой прямоугольник
  tile: { type: Object, required: true },
})

// narrow — половина ряда (сетка 2×1 в макете), wide — во всю ширину колонки
const isNarrow = computed(() => props.tile.shape === 'narrow')

// Яркость рамки, цвет иконки и внутренний отступ: активный разбор — главное
// на экране, поэтому у него полное золото рамки и больше воздуха внутри.
// Заливки не трогаем вообще, фон у всех плиток один (правило из макета:
// блоки различаются только рамкой)
const toneClass = computed(() =>
  props.tile.tone === 'active'
    ? 'border-gold text-gold p-5'
    : 'border-line-dim text-gold-dim p-4 hover:border-line-gold',
)
</script>

<template>
  <!-- Корень — сама ссылка, а не <div> внутри неё: так вся плитка кликается и
       получает фокус с клавиатуры одним элементом.
       :class массивом — Tailwind не умеет собирать имена классов из кусков
       строки, поэтому варианты пишутся целыми именами и выбираются здесь.
       hover: — состояние наведения мышью; на телефоне его нет, а на десктопе
       VK неподсвечивающаяся плитка читается как неживая -->
  <RouterLink
    :to="tile.to"
    class="flex min-w-0 rounded-card border bg-bg-card transition-colors"
    :class="[toneClass, isNarrow ? 'flex-col gap-3' : 'items-center gap-4']"
  >
    <!-- shrink-0 запрещает flex сплющивать иконку, когда текст рядом длинный.
         Цвет иконка берёт от родителя (currentColor), он задан в toneClass -->
    <ReportIcon :name="tile.icon" class="shrink-0" :class="isNarrow ? 'w-9' : 'w-11'" />

    <!-- min-w-0 — тот самый костыль, без которого длинное слово не переносится,
         а распирает flex-строку: по умолчанию элемент не даёт себя сжать уже
         своего содержимого -->
    <div class="min-w-0">
      <p class="font-display text-ink" :class="isNarrow ? 'text-lead' : 'text-title'">
        {{ tile.title }}
      </p>
      <p class="mt-1 font-sans text-meta text-ink-muted">{{ tile.subtitle }}</p>
    </div>
  </RouterLink>
</template>
