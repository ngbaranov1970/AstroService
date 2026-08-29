<script setup>
// Шаг 1: дата рождения. Показывает его OnboardingView, значение отдаёт
// строкой 'YYYY-MM-DD' через v-model. Своего состояния не держит.

import { computed, useTemplateRef } from 'vue'

const model = defineModel({ type: String, default: '' })

const inputEl = useTemplateRef('input')

/** Открыть системный календарь по нажатию на поле. */
function openPicker() {
  // Клик по прозрачному input на десктопе только ставит фокус: календарь
  // там открывает иконка справа, а её не видно. Зовём выбор сами
  try {
    inputEl.value?.showPicker()
  } catch {
    // Метода нет (старый движок) или пикер уже открыт самим браузером —
    // в обоих случаях поле остаётся в фокусе и принимает ввод с клавиатуры
  }
}

// Родительный падеж: «14 ноября», а не «14 ноябрь». Штатный Intl умеет так
// же, но приписывает « г.» в конце — проще держать список, чем вырезать
const MONTHS = [
  'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
  'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
]

// Сегодня в местной зоне в виде 'YYYY-MM-DD'. Локаль 'sv-SE' выбрана не
// ради Швеции: её формат даты совпадает с ISO, а toISOString() перевёл бы
// время в UTC и вечером 31-го числа вернул завтрашний день
const today = new Date().toLocaleDateString('sv-SE')

// Дату разбираем строкой, не через new Date(): конструктор трактует
// 'YYYY-MM-DD' как UTC-полночь и в минусовых поясах сдвигает день назад
const pretty = computed(() => {
  if (!model.value) return ''
  const [year, month, day] = model.value.split('-')
  return `${Number(day)} ${MONTHS[Number(month) - 1]} ${year}`
})
</script>

<template>
  <div class="w-full text-center">
    <h1 class="font-display text-display text-ink">Начнём с даты рождения</h1>
    <p class="mt-3 font-display text-lead text-ink-muted italic">
      По ней рассчитаем положение планет — до градуса
    </p>

    <!-- Поле из макета: своего вида у системного выбора даты нет, поэтому
         рисуем текст сами, а настоящий <input type="date"> кладём поверх
         прозрачным (inset-0 — растянуть на весь родительский блок).
         Тап попадает в него и открывает системный календарь, на экране
         остаётся наша вёрстка. min/max отдают проверку границ браузеру:
         раньше 1900-го карту не считаем, дата в будущем — опечатка -->
    <div class="relative mt-10" @click="openPicker">
      <p
        class="border-b border-line-gold pb-3 font-display text-2xl"
        :class="pretty ? 'text-ink' : 'text-ink-muted/50'"
      >
        {{ pretty || 'Выберите дату' }}
      </p>
      <input
        ref="input"
        v-model="model"
        type="date"
        min="1900-01-01"
        :max="today"
        class="absolute inset-0 h-full w-full cursor-pointer opacity-0"
      />
    </div>
  </div>
</template>
