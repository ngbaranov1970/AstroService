<script setup>
// Шаг 4: подпись карты. Показывает его OnboardingView, отдаёт строку через
// v-model. Поле необязательное — пустое значение мастер превратит в null.

import AppInput from '@/shared/ui/AppInput.vue'

const model = defineModel({ type: String, default: '' })
</script>

<template>
  <div class="w-full text-center">
    <h1 class="font-display text-display text-ink">Как подписать карту?</h1>
    <p class="mt-3 font-display text-lead text-ink-muted italic">
      Нужно только чтобы не путать карты в списке. В разбор имя не идёт
    </p>

    <div class="mt-10">
      <AppInput v-model="model" placeholder="Например, Маша" :maxlength="64" />
      <p class="mt-4 text-meta text-ink-muted">Можно не заполнять</p>
    </div>
  </div>
</template>
