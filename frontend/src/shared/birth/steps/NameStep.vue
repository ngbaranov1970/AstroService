<script setup>
// Шаг 4: подпись карты. Показывает его BirthWizard, отдаёт строку через
// v-model. Поле необязательное — пустое значение мастер превратит в null.
//
// Тексты приходят пропами: у натальной карты имя в разбор не идёт, а у
// стороны пары идёт (оно там этикетка «где чьё»), и обещать обратное нельзя.

import AppInput from '@/shared/ui/AppInput.vue'

const model = defineModel({ type: String, default: '' })

defineProps({
  // Значения по умолчанию — тексты онбординга: он про них ничего не знает
  // и передавать их не должен
  title: { type: String, default: 'Как подписать карту?' },
  hint: {
    type: String,
    default: 'Нужно только чтобы не путать карты в списке. В разбор имя не идёт',
  },
})
</script>

<template>
  <div class="w-full text-center">
    <h1 class="font-display text-display text-ink">{{ title }}</h1>
    <p class="mt-3 font-display text-lead text-ink-muted italic">{{ hint }}</p>

    <div class="mt-10">
      <AppInput v-model="model" placeholder="Например, Маша" :maxlength="64" />
      <p class="mt-4 text-meta text-ink-muted">Можно не заполнять</p>
    </div>
  </div>
</template>
