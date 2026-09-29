<script setup>
// Экран «Гороскоп в личку» — цель плитки меню сообщества (#horoscope).
// Показывает его роутер по адресу /horoscope, снаружи данных не принимает.
// Открывается и без карты: тогда сначала спрашивает одну дату и узнаёт знак
// (docs/astro-context.md, «Знак для рассылки — без карты»). Кнопки подписки
// не свои — их рисует GroupCard.

import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { goalRoute } from '@/app/entry'
import GroupCard from '@/features/community/GroupCard.vue'
import DateStep from '@/shared/birth/steps/DateStep.vue'
import { useCharts } from '@/shared/composables/useCharts'
import { useGroup } from '@/shared/composables/useGroup'
import AppButton from '@/shared/ui/AppButton.vue'

// Роутер — чтобы уводить в онбординг кнопкой: AppButton сам никуда не ведёт
const router = useRouter()
const { current } = useCharts()
// Те же status и available, что читает GroupCard: состояние общее на
// приложение, второго запроса к серверу не будет
const { status, available, sunSign, enterBirthDate, ensureLoaded } = useGroup()

// Знак известен: из своей карты или введённый датой. Карта главнее — так же
// выбирает и рассылка на сервере
const knowsSign = computed(() => Boolean(current.value || sunSign.value))

// Показывать ли поле даты: карты нет, знака нет, а записать его есть куда —
// приложение открыто из VK и состояние сообщества уже пришло
const needsDate = computed(
  () => !knowsSign.value && status.value === 'ready' && available.value,
)

// Форма даты. birthDate — 'YYYY-MM-DD' от DateStep, пусто — дата не собрана
const birthDate = ref('')
const busy = ref(false)
const error = ref(null)
// Дата попала на день смены знака: вместо формы — объяснение и выход в онбординг
const cuspDay = ref(false)

// Онбординг с возвратом сюда же: после первой карты он приведёт обратно
const onboardingRoute = { name: 'onboarding', query: { next: 'horoscope' } }

/** Отправить дату и узнать знак. Знак сам появится на экране через sunSign
 *  из useGroup; здесь только кнопка, ошибка и день смены знака. */
async function submitDate() {
  // Защита от двойного нажатия и от пустой даты
  if (!birthDate.value || busy.value) return

  busy.value = true
  error.value = null
  try {
    const sign = await enterBirthDate(birthDate.value)
    // null — день смены знака: одной даты мало, нужны время и место
    if (sign === null) cuspDay.value = true
  } catch (e) {
    // e.message — готовая фраза для человека из enterBirthDate
    error.value = e.message
  } finally {
    // finally выполняется в любом случае — и после успеха, и после ошибки
    busy.value = false
  }
}

// GroupCard прячется целиком, когда сообщество недоступно (открыто не из VK).
// Тогда экран остался бы с одним заголовком — показываем вместо блока строку.
// Пока идёт запрос, молчим: мигнувшая строка «открой из VK» пугает зря
const unavailable = computed(
  () => (status.value === 'ready' || status.value === 'error') && !available.value,
)

// Ссылка на бесплатный разбор текущей карты. Тот же маршрут, что у плитки
// «Твоя карта рождения», — разбор начнёт писаться сразу при открытии
const natalRoute = computed(() => goalRoute('natal', current.value?.token ?? null))

// GroupCard позовёт это и сам; второй вызов безопасен (защита от повторов
// внутри), а без него unavailable не узнал бы о загрузке, если карточка
// не смонтировалась
onMounted(ensureLoaded)
</script>

<template>
  <div class="flex flex-1 flex-col">
    <!-- Шапка как у сборки пары: метка раздела слева, «Назад» справа.
         RouterLink, а не router.back(): по ссылке из меню сообщества истории
         нет, и «назад» выбросил бы человека из приложения -->
    <header class="mb-7 flex items-baseline justify-between gap-3">
      <p class="min-w-0 truncate font-sans text-label tracking-label text-gold-dim uppercase">
        Рассылка
      </p>
      <RouterLink
        :to="{ name: 'home' }"
        class="shrink-0 font-sans text-label tracking-label text-gold-dim uppercase"
      >
        ← Назад
      </RouterLink>
    </header>

    <!-- Три ветки — на экране всегда ровно одна. v-if / v-else-if / v-else
         работают как if / elif / else: Vue рисует первую, чьё условие верно.
         <template> — обёртка без своего тега: группирует элементы под одно
         условие и в страницу не попадает -->

    <!-- 1. Дата попала на день смены знака. Сразу не перебрасываем: человек
         не понял бы, почему оказался в другом мастере -->
    <template v-if="cuspDay">
      <h1 class="font-display text-display text-ink">Нужны время и место</h1>
      <p class="mt-2 font-read text-lead text-ink-muted">
        В твой день рождения Солнце переходило из знака в знак. По одной дате не понять, какой из
        двух твой, — посчитаем карту целиком, со временем и местом рождения.
      </p>
      <!-- router.push — переход кодом, как клик по ссылке -->
      <AppButton class="mt-8" @click="router.push(onboardingRoute)">Посчитать карту</AppButton>
    </template>

    <!-- 2. Карты нет, знака нет — спрашиваем одну дату. Заголовок экрана
         здесь несёт сам DateStep: у него свой крупный h1 -->
    <template v-else-if="needsDate">
      <!-- v-model на компоненте: DateStep отдаёт собранную дату в birthDate
           и получает её обратно — двусторонняя связь, как у <input> -->
      <DateStep
        v-model="birthDate"
        title="Твой гороскоп на каждый день"
        hint="Узнаем знак по дате рождения — время и город не нужны"
      />
      <!-- Атрибуты в kebab-case (loading-text) — так в шаблонах принято
           писать пропы, объявленные в camelCase (loadingText): Vue их сводит -->
      <AppButton
        class="mt-4"
        :disabled="!birthDate"
        :loading="busy"
        loading-text="Узнаём знак…"
        @click="submitDate"
      >
        Узнать знак
      </AppButton>
      <p v-if="error" class="mt-3 text-center font-sans text-meta text-ink-muted">{{ error }}</p>
    </template>

    <!-- 3. Знак известен (или спросить его нечем) — экран подписки -->
    <template v-else>
      <h1 class="font-display text-display text-ink">Твой гороскоп на каждый день</h1>
      <p class="mt-2 font-read text-lead text-ink-muted">
        Одно сообщение утром в личные сообщения ВКонтакте. Без спама — отключить можно в любой
        момент этой же кнопкой.
      </p>

      <!-- Знак, введённый датой. {{ }} — вывод значения в текст, как
           f-строка. У кого карта, строки нет: знак он видит в своей карте -->
      <p v-if="!current && sunSign" class="mt-6 font-display text-lead text-gold">
        Твой знак — {{ sunSign }}
      </p>

      <!-- Подписка и разрешение писать. Блок сам решает, какие кнопки нужны
           этому человеку, и сам прячется, если нажимать их здесь нечем -->
      <GroupCard class="mt-8" />

      <p v-if="unavailable" class="mt-8 font-sans text-meta text-ink-muted">
        Включить рассылку можно только из приложения внутри ВКонтакте — открой его из нашего
        сообщества.
      </p>

      <!-- Выход в воронку: с картой — к бесплатному разбору, без карты — в
           онбординг, после которого человек попадёт на тот же разбор -->
      <div class="mt-auto pt-8 text-center">
        <RouterLink
          :to="current ? natalRoute : { name: 'onboarding', query: { next: 'natal' } }"
          class="font-sans text-label tracking-label text-gold uppercase underline-offset-4 active:underline"
        >
          {{ current ? 'А пока — твоя карта рождения →' : 'Посчитать карту целиком →' }}
        </RouterLink>
      </div>
    </template>
  </div>
</template>
