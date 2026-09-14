<script setup>
// Шаг 1: дата рождения. Показывает его OnboardingView, значение отдаёт
// строкой 'YYYY-MM-DD' через v-model. Три отдельных поля вместо системного
// календаря: дату рождения не выбирают листанием, её набирают по памяти.

import { computed, ref, useTemplateRef, watch } from 'vue'

const model = defineModel({ type: String, default: '' })

// Два падежа: в списке месяц стоит сам по себе («ноябрь»), в подписи под
// полями — при числе («14 ноября»)
const MONTHS_NOM = [
  'январь', 'февраль', 'март', 'апрель', 'май', 'июнь',
  'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь',
]
const MONTHS_GEN = [
  'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
  'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
]

const MIN_YEAR = 1900

// Сегодняшняя полночь. С ней сравниваем введённое: родиться сегодня можно,
// завтра — уже опечатка
const now = new Date()
const TODAY = new Date(now.getFullYear(), now.getMonth(), now.getDate())

// Поля держим строками: пустое поле — это '', а не 0, и ведущий ноль в «05»
// не должен пропадать сам по себе прямо во время набора
const day = ref('')
const month = ref('') // '1'…'12' — номер месяца, значение выбранного <option>
const year = ref('')

const monthEl = useTemplateRef('month')
const yearEl = useTemplateRef('year')

// Пришли со второго шага назад — показываем то, что уже введено. Компонент
// при возврате создаётся заново (в мастере он под v-if), своё состояние не
// переживает, и восстановить его можно только из модели
if (model.value) {
  const [y, m, d] = model.value.split('-')
  year.value = y
  month.value = String(Number(m))
  day.value = String(Number(d))
}

// Разбор трёх полей: либо готовая дата, либо причина, почему её нет.
// Одной computed, а не тремя: и дата, и текст ошибки — результат одной и той
// же проверки, разнесённые они разъедутся
const checked = computed(() => {
  const d = Number(day.value)
  const m = Number(month.value)
  const y = Number(year.value)

  // Человек ещё набирает — молчим: ругаться на незаконченный ввод некрасиво
  if (!day.value || !month.value || year.value.length < 4) {
    return { date: null, error: null }
  }

  if (y < MIN_YEAR) return { date: null, error: `Раньше ${MIN_YEAR} года карту не считаем` }

  const probe = new Date(y, m - 1, d)
  // Date молча «чинит» 31 февраля в 3 марта — сверяем, что числа не уехали
  if (probe.getDate() !== d || probe.getMonth() !== m - 1) {
    return { date: null, error: 'Такой даты не существует' }
  }
  if (probe > TODAY) return { date: null, error: 'Эта дата ещё не наступила' }

  return { date: probe, error: null }
})

// Собранная дата человеческими словами — подтверждение, что поля поняты так,
// как он задумал
const pretty = computed(() => {
  const date = checked.value.date
  if (!date) return ''
  return `${date.getDate()} ${MONTHS_GEN[date.getMonth()]} ${date.getFullYear()}`
})

/** Число в две цифры: 5 → '05'. */
function pad(value) {
  // padStart дополняет строку слева до нужной длины — как zfill(2) в Python
  return String(value).padStart(2, '0')
}

// Наверх отдаём только настоящую дату. Пока она не собралась, модель пустая,
// и кнопка «Дальше» у мастера остаётся заблокированной — отдельного признака
// «поле заполнено верно» ему не нужно
watch(
  () => checked.value.date,
  (date) => {
    model.value = date
      ? `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
      : ''
  },
)

/** Оставить в поле только цифры, не больше max, и вернуть результат. */
function onlyDigits(event, max) {
  // \D — «не цифра»; replace с флагом g вычищает все такие символы разом
  const clean = event.target.value.replace(/\D/g, '').slice(0, max)
  // Пишем очищенное и обратно в само поле. Если набрали букву, наша строка не
  // изменилась, Vue перерисовывать нечего — и буква осталась бы на экране
  event.target.value = clean
  return clean
}

function onDayInput(event) {
  day.value = onlyDigits(event, 2)
  // Двух цифр хватило — идём дальше. Одной цифры хватает с 4: месяца, где
  // есть 40-е число, не бывает, ждать второй цифры незачем
  if (day.value.length === 2 || Number(day.value) > 3) monthEl.value?.focus()
}

function onYearInput(event) {
  year.value = onlyDigits(event, 4)
}

function onMonthChange() {
  // Год ещё пуст — ставим курсор туда: на мобильном это сразу открывает
  // клавиатуру, лишний тап не нужен
  if (!year.value) yearEl.value?.focus()
}
</script>

<template>
  <div class="w-full text-center">
    <h1 class="font-display text-display text-ink">Начнём с даты рождения</h1>
    <p class="mt-3 font-display text-lead text-ink-muted italic">
      По ней рассчитаем положение планет — до градуса
    </p>

    <!-- Три поля в ряд: день узкий, месяц забирает остаток, год под четыре
         цифры. Каждое завёрнуто в <label>, поэтому нажатие на подпись под
         полем тоже ставит в него курсор -->
    <div class="mt-10 flex items-start gap-3">
      <label class="flex w-14 shrink-0 flex-col">
        <!-- :value + @input вместо v-model: значение чистим от нецифр по
             дороге, а v-model записал бы в поле всё подряд.
             inputmode="numeric" просит у мобильной клавиатуры цифры -->
        <input
          :value="day"
          type="text"
          inputmode="numeric"
          placeholder="14"
          autocomplete="off"
          class="w-full border-b border-line-gold bg-transparent pb-3 text-center font-display text-2xl text-ink caret-gold placeholder:text-ink-muted/40 focus:outline-none"
          @input="onDayInput"
        />
        <span class="mt-2 text-label tracking-label text-ink-muted/60 uppercase">день</span>
      </label>

      <label class="flex flex-1 flex-col">
        <!-- appearance-none снимает системную стрелку и рамку выпадашки.
             Список рисует сама система, поэтому цвет на <option> задан
             отдельно: наш тёмный фон на него не распространяется -->
        <select
          ref="month"
          v-model="month"
          class="w-full appearance-none border-b border-line-gold bg-transparent pb-3 text-center font-display text-2xl focus:outline-none"
          :class="month ? 'text-ink' : 'text-ink-muted/40'"
          @change="onMonthChange"
        >
          <option value="" disabled>месяц</option>
          <option
            v-for="(name, index) in MONTHS_NOM"
            :key="name"
            :value="String(index + 1)"
            class="bg-bg-card text-ink"
          >
            {{ name }}
          </option>
        </select>
        <span class="mt-2 text-label tracking-label text-ink-muted/60 uppercase">месяц</span>
      </label>

      <label class="flex w-24 shrink-0 flex-col">
        <input
          ref="year"
          :value="year"
          type="text"
          inputmode="numeric"
          placeholder="1975"
          autocomplete="off"
          class="w-full border-b border-line-gold bg-transparent pb-3 text-center font-display text-2xl text-ink caret-gold placeholder:text-ink-muted/40 focus:outline-none"
          @input="onYearInput"
        />
        <span class="mt-2 text-label tracking-label text-ink-muted/60 uppercase">год</span>
      </label>
    </div>

    <!-- Строка под полями держит высоту всегда (min-h), иначе появление
         подписи дёргало бы вёрстку вверх-вниз при каждом наборе -->
    <p class="mt-6 min-h-6 font-display text-lead" :class="pretty ? 'text-gold' : 'text-ink-muted'">
      {{ pretty || checked.error }}
    </p>
  </div>
</template>
