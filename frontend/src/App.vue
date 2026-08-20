<script setup>
// Корневой компонент: оболочка приложения и гейт входа.
// Показывает его main.js. Данных снаружи не принимает — личность добывает сам
// через useSession, а экраны появятся здесь на месте временного блока проверки.

import { onMounted, ref } from 'vue'

import { platform } from '@/app/platform'
import { listCharts } from '@/shared/api/endpoints'
import { useSession } from '@/shared/composables/useSession'

// Деструктуризация: забираем из возвращённого объекта нужные поля по именам —
// то же самое, что a, b = d['a'], d['b'] в Python, только короче
const { status, error, isLoading, user, start } = useSession()

// Временное состояние проверки каркаса: сюда ляжет ответ GET /charts
const charts = ref(null)
const chartsError = ref(null)

/** Войти и, если вход удался, дёрнуть первый защищённый запрос. */
async function boot() {
  chartsError.value = null
  // start() не бросает исключений: результат читается из status
  await start()
  if (status.value !== 'ready') return

  try {
    charts.value = await listCharts()
  } catch (failure) {
    chartsError.value = failure.message
  }
}

// onMounted — «когда компонент появился на экране». Ближайшая аналогия из
// Python: не __init__, а первый вызов после того, как объект встал на место
onMounted(boot)
</script>

<template>
  <!-- Три состояния гейта. v-if / v-else-if / v-else — обычное ветвление,
       только выбирает не значение, а кусок разметки: невыбранных в странице
       нет вовсе (это не «спрятать через display:none») -->
  <div class="flex min-h-dvh flex-col px-5 py-6">
    <!-- 1. Вход идёт. Ничего осмысленного показать нельзя: карт мы ещё не
         видели, и рисовать пустые экраны — врать пользователю -->
    <div v-if="isLoading" class="flex flex-1 items-center justify-center">
      <p class="animate-pulse font-display text-title text-ink-muted">Соединяемся…</p>
    </div>

    <!-- 2. Вход не удался: подпись не сошлась, сеть отвалилась или на сервере
         не настроен вход из VK. Текст готовит useSession, здесь только показ -->
    <div v-else-if="status === 'error'" class="flex flex-1 flex-col items-center justify-center gap-4 text-center">
      <h1 class="font-display text-title text-ink">Не получилось войти</h1>
      <!-- {{ }} — подстановка значения в текст, как f-строка в Python.
           У ref в шаблоне .value не пишут: Vue разворачивает его сам -->
      <p class="text-meta text-ink-muted">{{ error }}</p>

      <!-- Кнопка-заглушка: на шаге вёрстки станет компонентом AppButton
           варианта outline (золотая рамка, капс, во всю ширину) -->
      <button
        class="mt-2 rounded-card border border-line-gold px-6 py-3 font-sans text-label tracking-label text-gold uppercase"
        @click="boot"
      >
        Повторить
      </button>
    </div>

    <!-- 3. Вход прошёл. Здесь на шаге 2 встанет <router-view /> с экранами,
         а пока — проверка каркаса: видно ли API и что оно отвечает -->
    <div v-else class="flex flex-1 flex-col gap-4">
      <p class="font-sans text-label tracking-label text-gold-dim uppercase">
        Каркас · платформа {{ platform.name }}
      </p>

      <h1 class="font-display text-display text-ink">
        <!-- ?. — «возьми поле, если объект есть»; иначе undefined вместо падения.
             ?? — «слева null или undefined → возьми правое» -->
        {{ user?.name ?? 'Без имени' }}
      </h1>

      <p v-if="chartsError" class="text-meta text-ink-muted">GET /charts: {{ chartsError }}</p>
      <p v-else-if="charts" class="text-meta text-ink-muted">
        GET /charts: {{ charts.length }} карт(ы)
      </p>

      <!-- v-for — цикл по списку. :key нужен Vue, чтобы при изменении списка
           перерисовать изменившееся, а не всё подряд; ключ обязан быть
           уникальным, поэтому берём token карты -->
      <ul class="flex flex-col gap-2">
        <li
          v-for="chart in charts ?? []"
          :key="chart.token"
          class="rounded-card border border-line-dim p-3 text-meta text-ink-muted"
        >
          {{ chart.person_name ?? 'без имени' }} · {{ chart.birth_local }} · {{ chart.place_name }}
        </li>
      </ul>
    </div>
  </div>
</template>
