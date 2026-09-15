<script setup>
// Экран сборки пары: моя карта + тип связи + партнёр. Показывает его роутер
// по адресу /pair/new, снаружи данных не принимает. Собранную пару отдаёт
// серверу и уводит на /pair/:token — ожиданием партнёра, расчётом и текстами
// занимается уже PairView.
//
// Партнёра задают тремя способами: выбрать из своих карт, ввести данные
// вручную или позвать ссылкой. Первый способ есть не всегда — у человека с
// одной картой выбирать не из чего.

import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

import { createChart } from '@/shared/api/endpoints'
import BirthWizard from '@/shared/birth/BirthWizard.vue'
import { useCharts } from '@/shared/composables/useCharts'
import { usePairs } from '@/shared/composables/usePairs'
import AppButton from '@/shared/ui/AppButton.vue'
import AppInput from '@/shared/ui/AppInput.vue'
import { KINDS } from './pairKinds'

const router = useRouter()
const { charts, current, remember } = useCharts()
const { create } = usePairs()

// ─── Что человек выбрал ────────────────────────────────────────────────────

// Моя сторона. Начинаем с той карты, вокруг которой крутится приложение:
// в паре «я и кто-то» первая сторона почти всегда она
const chartAToken = ref(current.value?.token ?? null)

// Тип связи. null — ещё не выбран, и это не мелочь: промпт, по умолчанию
// предполагающий влечение, на паре «я и мама» выдаст дикость
const kind = ref(null)

// Способ задать партнёра: chart | manual | invite. null — не выбран
const partnerMode = ref(null)
const chartBToken = ref(null)

// Как назвать себя партнёру на анонимном экране приглашения. Отдельное поле,
// а не имя карты: имя карты человек вводил про себя и для себя, и отдавать
// его третьему лицу нельзя
const inviterLabel = ref('')

// Идёт отправка / отказ сервера. Общие на обе ветки отправки: и быстрая
// сборка из готовых карт, и длинная через мастер кончаются одним POST /pairs
const submitting = ref(false)
const error = ref(null)

// Открыт ли поверх формы мастер ввода данных партнёра. Отдельный флаг, а не
// значение partnerMode: из мастера человек может вернуться назад, и
// выбранный способ при этом обязан остаться выбранным
const wizardOpen = ref(false)

// ─── Производные значения ──────────────────────────────────────────────────

const chartA = computed(() => charts.value.find((item) => item.token === chartAToken.value) ?? null)

// Карты, которые годятся второй стороной: все, кроме выбранной первой.
// Пару карты с самой собой сервер и так отвергнет, но объяснять человеку
// отказ там, где выбор можно просто не показывать, — плохой обмен
const partnerCharts = computed(() =>
  charts.value.filter((item) => item.token !== chartAToken.value),
)

/** Подпись карты в списке: дата рождения и город — как в переключателе карт. */
function birthLine(chart) {
  const date = new Intl.DateTimeFormat('ru-RU', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  }).format(new Date(chart.birth_local))

  return chart.place_name ? `${date} · ${chart.place_name}` : date
}

// Кнопка отправки доступна, когда состав пары собран целиком. У ветки
// «позову ссылкой» вторая сторона законно пустая — подпись при этом
// необязательна, без неё партнёр увидит безличное «тебя приглашают»
const canSubmit = computed(() => {
  if (chartAToken.value === null || kind.value === null) return false
  if (partnerMode.value === 'chart') return chartBToken.value !== null
  return partnerMode.value === 'invite'
})

// ─── Отправка ──────────────────────────────────────────────────────────────

/** Отправить состав пары и уйти на её экран.
 *
 *  partnerToken — токен карты второй стороны либо null для ветки приглашения.
 *  Ошибку показываем на месте: выбор уже сделан, терять его из-за обрыва
 *  сети нельзя. */
async function submitPair(partnerToken) {
  submitting.value = true
  error.value = null

  try {
    const pair = await create({
      chart_a_token: chartAToken.value,
      chart_b_token: partnerToken,
      kind: kind.value,
      // Подпись отправляем только там, где она вообще будет показана
      inviter_label: partnerToken === null ? inviterLabel.value.trim() || null : null,
    })

    // replace, а не push: возврат «назад» в форму сборки уже созданной пары
    // никому не нужен
    await router.replace({ name: 'pair', params: { token: pair.token } })
  } catch (failure) {
    error.value = failure.detail || failure.message
    // Мастер, если отправляли из него, закрываем: ошибка живёт на форме, и
    // показывать её под четвёртым шагом ввода данных бессмысленно
    closeWizard()
  } finally {
    submitting.value = false
  }
}

/** Кнопка «Собрать пару» — обе быстрые ветки: готовая карта или приглашение. */
function submit() {
  if (!canSubmit.value || submitting.value) return
  submitPair(partnerMode.value === 'chart' ? chartBToken.value : null)
}

// ─── Ручной ввод партнёра ──────────────────────────────────────────────────

/** Завести карту партнёра и сразу собрать с ней пару.
 *
 *  Два запроса подряд, а не один: карта партнёра — обычная карта в аккаунте
 *  инициатора, и заводит её тот же POST /charts, что и в онбординге.
 *  payload — готовое тело запроса, его собрал мастер. */
async function submitManual(payload) {
  submitting.value = true
  error.value = null

  try {
    const chart = await createChart(payload)
    // Кладём карту в общий список: она настоящая, и человек увидит её в
    // переключателе на главной
    remember(chart)
    await submitPair(chart.token)
  } catch (failure) {
    error.value = failure.detail || failure.message
    closeWizard()
  } finally {
    submitting.value = false
  }
}

/** Открыть мастер, а из него вернуться к форме. */
function openWizard() {
  partnerMode.value = 'manual'
  chartBToken.value = null
  error.value = null
  wizardOpen.value = true
}

/** Закрыть мастер — по «Отмене» или после отказа сервера. */
function closeWizard() {
  wizardOpen.value = false
  // Способ «вручную» с закрытым мастером — тупик: данных партнёра нет, и
  // кнопка отправки осталась бы неактивной без единого объяснения
  if (partnerMode.value === 'manual') partnerMode.value = null
}

/** Выбрать способ «из своих карт» или «позову ссылкой». */
function chooseMode(mode) {
  partnerMode.value = mode
  error.value = null
  // Выбранная карта относится ровно к одному способу: оставить её при
  // переключении на приглашение значило бы отправить состав, которого на
  // экране уже не видно
  if (mode !== 'chart') chartBToken.value = null
}

/** Уйти с экрана, ничего не собрав. */
function cancel() {
  router.replace({ name: 'home' })
}
</script>

<template>
  <!-- Мастер показывается вместо формы, а не поверх неё: у него свой полный
       экран с шагами, и форма под ним всё равно не видна -->
  <BirthWizard
    v-if="wizardOpen"
    :busy="submitting"
    :error="error"
    :can-cancel="true"
    submit-label="Собрать пару"
    busy-label="Собираем пару…"
    name-title="Как зовут партнёра?"
    name-hint="Имя появится в разборе — так видно, где чьё"
    @submit="submitManual"
    @cancel="closeWizard"
  />

  <!-- Форма сборки: три вопроса подряд сверху вниз -->
  <div v-else class="flex flex-1 flex-col">
    <header class="mb-7 flex items-baseline justify-between gap-3">
      <p class="min-w-0 truncate font-sans text-label tracking-label text-gold-dim uppercase">
        Совместимость
      </p>
      <button
        type="button"
        class="shrink-0 font-sans text-label tracking-label text-gold-dim uppercase"
        @click="cancel"
      >
        ← Назад
      </button>
    </header>

    <h1 class="font-display text-display text-ink">Кто с кем?</h1>
    <p class="mt-2 font-read text-lead text-ink-muted">
      Сравним две карты и расскажем, что между вами происходит.
    </p>

    <!-- 1. Моя сторона. У человека с одной картой выбирать не из чего —
         тогда это просто строка, а не список из одной кнопки -->
    <section class="mt-8">
      <p class="font-sans text-label tracking-label text-gold-dim uppercase">Твоя сторона</p>

      <p v-if="charts.length === 1" class="mt-3 font-display text-title text-ink">
        {{ chartA?.person_name || 'Твоя карта' }}
      </p>

      <div v-else class="mt-3 space-y-2">
        <button
          v-for="chart in charts"
          :key="chart.token"
          type="button"
          class="block w-full rounded-card border bg-bg-card p-4 text-left transition-colors"
          :class="
            chart.token === chartAToken ? 'border-line-gold' : 'border-line-dim active:bg-gold/5'
          "
          @click="chartAToken = chart.token"
        >
          <p
            class="truncate font-display text-lead"
            :class="chart.token === chartAToken ? 'text-gold' : 'text-ink'"
          >
            {{ chart.person_name || 'Без имени' }}
          </p>
          <p class="truncate font-sans text-meta text-ink-muted">{{ birthLine(chart) }}</p>
        </button>
      </div>
    </section>

    <!-- 2. Тип связи. Обязателен: на паре «я и мама» разбор про влечение —
         готовая жалоба, и умолчания здесь быть не может -->
    <section class="mt-8">
      <p class="font-sans text-label tracking-label text-gold-dim uppercase">Что вас связывает</p>

      <div class="mt-3 grid grid-cols-2 gap-2">
        <button
          v-for="option in KINDS"
          :key="option.value"
          type="button"
          class="rounded-card border bg-bg-card p-4 text-left transition-colors"
          :class="
            option.value === kind ? 'border-line-gold' : 'border-line-dim active:bg-gold/5'
          "
          @click="kind = option.value"
        >
          <p
            class="font-display text-lead"
            :class="option.value === kind ? 'text-gold' : 'text-ink'"
          >
            {{ option.label }}
          </p>
          <p class="mt-1 font-sans text-meta text-ink-muted">{{ option.hint }}</p>
        </button>
      </div>
    </section>

    <!-- 3. Партнёр. Способов три, и первый есть не всегда -->
    <section class="mt-8">
      <p class="font-sans text-label tracking-label text-gold-dim uppercase">Кто второй</p>

      <div class="mt-3 space-y-2">
        <button
          v-if="partnerCharts.length"
          type="button"
          class="block w-full rounded-card border bg-bg-card p-4 text-left transition-colors"
          :class="
            partnerMode === 'chart' ? 'border-line-gold' : 'border-line-dim active:bg-gold/5'
          "
          @click="chooseMode('chart')"
        >
          <p
            class="font-display text-lead"
            :class="partnerMode === 'chart' ? 'text-gold' : 'text-ink'"
          >
            Из моих карт
          </p>
        </button>

        <button
          type="button"
          class="block w-full rounded-card border bg-bg-card p-4 text-left transition-colors"
          :class="
            partnerMode === 'manual' ? 'border-line-gold' : 'border-line-dim active:bg-gold/5'
          "
          @click="openWizard"
        >
          <p
            class="font-display text-lead"
            :class="partnerMode === 'manual' ? 'text-gold' : 'text-ink'"
          >
            Ввести данные
          </p>
          <p class="mt-1 font-sans text-meta text-ink-muted">Дата, время и место рождения</p>
        </button>

        <button
          type="button"
          class="block w-full rounded-card border bg-bg-card p-4 text-left transition-colors"
          :class="
            partnerMode === 'invite' ? 'border-line-gold' : 'border-line-dim active:bg-gold/5'
          "
          @click="chooseMode('invite')"
        >
          <p
            class="font-display text-lead"
            :class="partnerMode === 'invite' ? 'text-gold' : 'text-ink'"
          >
            Позвать по ссылке
          </p>
          <p class="mt-1 font-sans text-meta text-ink-muted">Партнёр введёт свои данные сам</p>
        </button>
      </div>

      <!-- Выбор второй карты раскрывается под способом, а не уводит на
           отдельный экран: список короткий, а возвращаться в форму после
           перехода человеку пришлось бы вручную -->
      <div v-if="partnerMode === 'chart'" class="mt-3 space-y-2">
        <button
          v-for="chart in partnerCharts"
          :key="chart.token"
          type="button"
          class="block w-full rounded-card border bg-bg-card p-4 text-left transition-colors"
          :class="
            chart.token === chartBToken ? 'border-line-gold' : 'border-line-dim active:bg-gold/5'
          "
          @click="chartBToken = chart.token"
        >
          <p
            class="truncate font-display text-lead"
            :class="chart.token === chartBToken ? 'text-gold' : 'text-ink'"
          >
            {{ chart.person_name || 'Без имени' }}
          </p>
          <p class="truncate font-sans text-meta text-ink-muted">{{ birthLine(chart) }}</p>
        </button>
      </div>

      <!-- Подпись для партнёра и предупреждение о том, что он увидит.
           Предупреждение обязательное, а не вежливое: человек отправляет
           ссылку на данные двоих (152-ФЗ) -->
      <div v-if="partnerMode === 'invite'" class="mt-5">
        <AppInput v-model="inviterLabel" placeholder="Как тебя назвать?" :maxlength="64" />
        <p class="mt-4 text-center font-sans text-meta text-ink-muted">
          Партнёр увидит это имя и общий разбор. Его данные сохранятся в твоём аккаунте.
        </p>
      </div>
    </section>

    <!-- Кнопка прижата к низу: mt-auto съедает весь свободный остаток -->
    <p v-if="error" class="mt-8 text-center font-sans text-meta text-ink-muted">{{ error }}</p>

    <div class="mt-auto pt-8">
      <AppButton
        :disabled="!canSubmit"
        :loading="submitting"
        loading-text="Собираем пару…"
        @click="submit"
      >
        {{ partnerMode === 'invite' ? 'Получить ссылку' : 'Собрать пару' }}
      </AppButton>
    </div>
  </div>
</template>
