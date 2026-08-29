<script setup>
// Онбординг: мастер из четырёх шагов (дата → время → город → имя), который
// заводит первую карту. Показывает его роутер по адресу /onboarding, снаружи
// данных не принимает. Всё состояние формы держит этот файл — шаги только
// рисуют своё поле и отдают значение назад через v-model.

import { computed, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { createChart } from '@/shared/api/endpoints'
import { useCharts } from '@/shared/composables/useCharts'
import AppButton from '@/shared/ui/AppButton.vue'
import StepDots from '@/shared/ui/StepDots.vue'
import SunEmblem from '@/shared/ui/SunEmblem.vue'
import DateStep from './steps/DateStep.vue'
import NameStep from './steps/NameStep.vue'
import PlaceStep from './steps/PlaceStep.vue'
import TimeStep from './steps/TimeStep.vue'

const TOTAL = 4

const router = useRouter()
const { remember } = useCharts()

const step = ref(1)
const submitting = ref(false)
const error = ref(null)

// reactive — тот же ref, но для объекта целиком: следим за всеми полями сразу
// и пишем form.birthDate без .value. Для одиночных значений берут ref, для
// формы из нескольких полей — reactive; смешивать в одном месте незачем
const form = reactive({
  birthDate: '', // 'YYYY-MM-DD' — ровно в таком виде отдаёт <input type="date">
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

const nextLabel = computed(() => (step.value < TOTAL ? 'Дальше' : 'Готово'))

/** Отправить форму и увести на главную. Ошибку показываем на месте:
 *  данные уже введены, терять их из-за обрыва сети нельзя. */
async function submit() {
  submitting.value = true
  error.value = null

  try {
    const chart = await createChart({
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

    // Кладём карту в общий список сразу: иначе guard роутера, увидев пустой
    // список, развернёт нас с главной обратно в онбординг
    remember(chart)
    // replace, а не push: возврат «назад» в мастер после создания карты
    // никому не нужен
    await router.replace({ name: 'home' })
  } catch (failure) {
    // detail — текст от нашего API (например, «Дата рождения в будущем»),
    // message — то, что осталось от обрыва сети
    error.value = failure.detail || failure.message
  } finally {
    submitting.value = false
  }
}

function next() {
  if (!canGoNext.value) return
  if (step.value < TOTAL) {
    step.value += 1
    return
  }
  submit()
}

function back() {
  if (step.value > 1) step.value -= 1
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

    <AppButton
      :disabled="!canGoNext"
      :loading="submitting"
      loading-text="Считаем карту…"
      @click="next"
    >
      {{ nextLabel }}
    </AppButton>

    <!-- Возврат на предыдущий шаг. На макете его нет, но без него опечатка в
         дате исправляется только перезапуском приложения -->
    <button
      v-if="step > 1"
      type="button"
      class="mt-4 py-2 font-sans text-label tracking-label text-gold-dim uppercase"
      @click="back"
    >
      Назад
    </button>
  </section>
</template>
