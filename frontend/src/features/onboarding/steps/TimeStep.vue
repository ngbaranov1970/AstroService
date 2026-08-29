<script setup>
// Шаг 2: время рождения. Показывает его OnboardingView. Отдаёт две вещи:
// само время строкой 'HH:MM' (v-model) и признак «не помню» (v-model:unknown).
// Признак отдельный, потому что пустое время и «времени нет» — разные случаи.

import { useTemplateRef } from 'vue'

const model = defineModel({ type: String, default: '' })
// Именованная модель: у родителя это v-model:unknown="form.timeUnknown"
const unknown = defineModel('unknown', { type: Boolean, default: false })

const inputEl = useTemplateRef('input')

/** Открыть системный выбор времени по нажатию на поле. */
function openPicker() {
  // Тот же случай, что на шаге даты: у прозрачного поля не видно иконки,
  // которой браузер открывает выбор — зовём его сами
  if (unknown.value) return
  try {
    inputEl.value?.showPicker()
  } catch {
    // Метода нет или пикер уже открыт — поле всё равно принимает ввод
  }
}

/** Переключить «не помню». Введённое время не стираем: передумает — вернётся. */
function toggleUnknown() {
  unknown.value = !unknown.value
}
</script>

<template>
  <div class="w-full text-center">
    <h1 class="font-display text-display text-ink">Во сколько вы родились?</h1>
    <p class="mt-3 font-display text-lead text-ink-muted italic">
      Время задаёт дома и Асцендент — без него разбор выйдет о характере, но
      не о сферах жизни
    </p>

    <!-- Тот же приём, что на шаге даты: видимый текст наш, системный выбор
         времени лежит поверх прозрачным. Когда время неизвестно, поле
         выключаем — pointer-events-none снимает с него нажатия -->
    <div class="relative mt-10" @click="openPicker">
      <p
        class="border-b pb-3 font-display text-2xl"
        :class="
          unknown
            ? 'border-line-dim text-ink-muted/50'
            : model
              ? 'border-line-gold text-ink'
              : 'border-line-gold text-ink-muted/50'
        "
      >
        {{ unknown ? 'Время неизвестно' : model || 'Выберите время' }}
      </p>
      <input
        ref="input"
        v-model="model"
        type="time"
        :disabled="unknown"
        class="absolute inset-0 h-full w-full cursor-pointer opacity-0 disabled:pointer-events-none"
      />
    </div>

    <!-- Переключатель, а не отдельная кнопка «пропустить»: человек должен
         видеть, в каком он состоянии, и уметь вернуться -->
    <button
      type="button"
      class="mt-6 py-2 font-sans text-label tracking-label uppercase"
      :class="unknown ? 'text-gold' : 'text-gold-dim'"
      @click="toggleUnknown"
    >
      {{ unknown ? '× Не помню время' : 'Не помню время' }}
    </button>
  </div>
</template>
