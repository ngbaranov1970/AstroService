<script setup>
// Мастер ввода данных рождения: четыре шага (дата → время → город → имя).
// Показывают его экраны: сейчас онбординг, дальше — сборка пары и приглашение
// партнёра. Что делать с собранными данными, мастер не знает: он отдаёт их
// наверх событием submit, а состояние отправки получает пропами.

import { computed, reactive, ref } from 'vue'

import AppButton from '@/shared/ui/AppButton.vue'
import StepDots from '@/shared/ui/StepDots.vue'
import SunEmblem from '@/shared/ui/SunEmblem.vue'
import DateStep from './steps/DateStep.vue'
import NameStep from './steps/NameStep.vue'
import PlaceStep from './steps/PlaceStep.vue'
import TimeStep from './steps/TimeStep.vue'

const TOTAL = 4

// defineProps — объявление того, что компонент принимает снаружи. Ближайшая
// аналогия: сигнатура функции, только аргументы передаются атрибутами в
// разметке родителя
const props = defineProps({
  // Идёт отправка: кнопка показывает спиннер и не нажимается второй раз
  busy: { type: Boolean, default: false },
  // Отказ от того, кто отправлял. Мастер его только показывает: что там
  // случилось, знает вызывающий экран
  error: { type: String, default: null },
  // Подписи последней кнопки: у карты это «Готово» и «Считаем карту…»,
  // у пары будут свои
  submitLabel: { type: String, default: 'Готово' },
  busyLabel: { type: String, default: 'Считаем карту…' },
  // Можно ли уйти с первого шага. У человека без единой карты уходить
  // некуда — guard роутера вернёт его сюда же
  canCancel: { type: Boolean, default: false },
})

// defineEmits — объявление событий, которые компонент шлёт наверх. Обратная
// сторона пропов: вниз идут данные, наверх — события
const emit = defineEmits(['submit', 'cancel'])

const step = ref(1)

// reactive — тот же ref, но для объекта целиком: следим за всеми полями сразу
// и пишем form.birthDate без .value. Для одиночных значений берут ref, для
// формы из нескольких полей — reactive
const form = reactive({
  birthDate: '', // 'YYYY-MM-DD' — ровно в таком виде его собирает DateStep
  birthTime: '', // 'HH:MM'
  timeUnknown: false, // «не помню время» → в API уедет birth_time: null
  place: null, // {label, lat, lon} — выбранная подсказка из GET /places
  personName: '',
})

// Кнопка «Дальше» заблокирована, пока шаг не заполнен. Имя необязательно,
// время может быть неизвестно — на этих шагах пройти можно всегда
const canGoNext = computed(() => {
  if (step.value === 1) return form.birthDate !== ''
  if (step.value === 2) return form.timeUnknown || form.birthTime !== ''
  if (step.value === 3) return form.place !== null
  return true
})

const nextLabel = computed(() => (step.value < TOTAL ? 'Дальше' : props.submitLabel))

// Нижняя кнопка делает разное на первом шаге и на остальных: там выход из
// мастера, тут возврат к предыдущему полю
const backLabel = computed(() => (step.value > 1 ? 'Назад' : 'Отмена'))
const showBack = computed(() => step.value > 1 || props.canCancel)

function next() {
  // Пока идёт отправка, повторное нажатие игнорируем: два запроса создадут
  // две одинаковые карты
  if (!canGoNext.value || props.busy) return

  if (step.value < TOTAL) {
    step.value += 1
    return
  }

  // Отдаём данные в той форме, в какой их ждёт API: оба экрана, которые зовут
  // мастер, отправляют этот объект на сервер как есть
  emit('submit', {
    // Пустое имя отправляем как null: на сервере пустая строка всё равно
    // станет NULL, но лучше не гонять по сети то, чего нет
    person_name: form.personName.trim() || null,
    birth_date: form.birthDate,
    birth_time: form.timeUnknown ? null : form.birthTime,
    // Координаты берём из подсказки, а не из набранного текста: одинаковых
    // названий много, а часовой пояс сервер считает именно по координатам
    lat: form.place.lat,
    lon: form.place.lon,
    place_name: form.place.label,
  })
}

function back() {
  if (step.value > 1) {
    step.value -= 1
    return
  }
  // Первый шаг: человек передумал заводить карту. Что делать с уходом,
  // решает экран — у онбординга это возврат на главную
  emit('cancel')
}
</script>

<template>
  <section class="flex flex-1 flex-col">
    <!-- Верхний блок с эмблемой и полем растянут (flex-1) и центрирован, а
         кнопка прижата к низу — композиция макета B_1 -->
    <div class="flex flex-1 flex-col items-center justify-center gap-7">
      <SunEmblem class="w-[45%] text-gold" />
      <StepDots :current="step" :total="TOTAL" />

      <!-- Шаги показываем ветвлением, а не динамическим компонентом: у
           каждого своя пара v-model, и явные строки читаются лучше.
           v-model="form.birthDate" — двусторонняя связь: шаг пишет значение
           прямо в нашу форму -->
      <DateStep v-if="step === 1" v-model="form.birthDate" />
      <TimeStep
        v-else-if="step === 2"
        v-model="form.birthTime"
        v-model:unknown="form.timeUnknown"
      />
      <PlaceStep v-else-if="step === 3" v-model="form.place" />
      <NameStep v-else v-model="form.personName" />
    </div>

    <!-- Отказ сервера: показываем над кнопкой, введённое не стираем -->
    <p v-if="error" class="mb-3 text-center text-meta text-ink-muted">{{ error }}</p>

    <AppButton :disabled="!canGoNext" :loading="busy" :loading-text="busyLabel" @click="next">
      {{ nextLabel }}
    </AppButton>

    <!-- Возврат на предыдущий шаг, а на первом — выход из мастера. На макете
         кнопки нет, но без неё опечатка в дате исправляется только
         перезапуском приложения, а брошенный мастер вообще не закрыть -->
    <button
      v-if="showBack"
      type="button"
      class="mt-4 py-2 font-sans text-label tracking-label text-gold-dim uppercase"
      @click="back"
    >
      {{ backLabel }}
    </button>
  </section>
</template>
