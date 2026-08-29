<script setup>
// Кнопка-рамка из макета: прозрачный фон, золотая рамка, капс с разрядкой.
// Заполненного варианта в макетах нет, поэтому вариантов у компонента нет тоже.
// Показывают её шаги онбординга и экраны разборов; текст приходит содержимым.

import { computed } from 'vue'

// defineProps — объявление «что компонент принимает снаружи». Это не импорт:
// такие define* видны в <script setup> сами собой, их разворачивает сборка.
// Объектная форма задаёт тип и значение по умолчанию — примерно как
// аргументы функции с аннотацией и дефолтом в Python
const props = defineProps({
  // Кнопка «Дальше» неактивна, пока шаг не заполнен
  disabled: { type: Boolean, default: false },
  // Идёт отправка: подменяем содержимое и не даём нажать второй раз
  loading: { type: Boolean, default: false },
  // Текст на время отправки — у каждого экрана свой («Считаем карту…»)
  loadingText: { type: String, default: 'Отправляем…' },
})

// Нажимать нельзя ни в том, ни в другом состоянии — считаем один раз здесь,
// чтобы в шаблоне не повторять условие дважды
const isLocked = computed(() => props.disabled || props.loading)
</script>

<template>
  <!-- Обработчик клика снаружи (@click="next") сюда специально не принимаем:
       у компонента один корневой элемент, и Vue сам вешает на него все
       «лишние» атрибуты и события родителя — это называется fallthrough.
       :disabled с настоящим <button> надёжнее любых классов: заблокированная
       кнопка не отдаёт клик и не ловит фокус с клавиатуры -->
  <button
    type="button"
    :disabled="isLocked"
    class="w-full rounded-card border px-6 py-4 font-sans text-label tracking-label uppercase transition-colors"
    :class="
      isLocked
        ? 'border-line-dim text-gold-dim'
        : 'border-line-gold text-gold active:bg-gold/10'
    "
  >
    <!-- <slot /> — дырка для содержимого: то, что родитель написал между
         тегами <AppButton>…</AppButton>, встанет сюда. Ближайшая аналогия
         из Python — не наследование, а передача куска разметки аргументом -->
    <slot v-if="!loading" />
    <span v-else class="animate-pulse">{{ loadingText }}</span>
  </button>
</template>
