<script setup>
// Разбор: один экран на все типы, вид зависит от status из API (available,
// running, ready, locked, failed). Показывает его роутер по адресу
// /report/:token/:type; данные берёт из useReports по этим двум параметрам.
//
// Ветвимся по статусу, а не по типу разбора: бэкенд может выпустить новый тип
// без релиза фронта, и этот экран покажет его без единой правки.

import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { platform } from '@/app/platform'
import GroupCard from '@/features/community/GroupCard.vue'
import { ERROR_CODE, REPORT_STATUS } from '@/shared/api/endpoints'
import { usePolling } from '@/shared/composables/usePolling'
import { useReports } from '@/shared/composables/useReports'
import AppButton from '@/shared/ui/AppButton.vue'

import FullReportTile from './FullReportTile.vue'
import ReportFeedback from './ReportFeedback.vue'
import ReportSections from './ReportSections.vue'

const route = useRoute()

// Чей это разбор — карты или пары — говорит сам маршрут (meta.scope).
// useReports зовём внутри computed, а не один раз при создании экрана: Vue
// Router переиспользует компонент между маршрутами, у которых он общий, и
// замыкание осталось бы ходить в прежние ручки
const scope = computed(() => route.meta.scope ?? 'chart')
const api = computed(() => useReports(scope.value))

// Адрес экрана. Параметры роутера бывают и массивом (это его контракт),
// поэтому приводим к строке явно — она уходит в запросы
const token = computed(() => String(route.params.token ?? ''))
const type = computed(() => String(route.params.type ?? ''))

// Наш разбор из общего списка владельца. null — либо список ещё не пришёл,
// либо сервер такого типа не отдаёт; эти случаи экран различает по listStatus
const current = computed(() => api.value.report(token.value, type.value))
const listStatus = computed(() => api.value.state(token.value).status)

const status = computed(() => current.value?.status ?? null)
const isRunning = computed(() => status.value === REPORT_STATUS.RUNNING)
const isLocked = computed(() => status.value === REPORT_STATUS.LOCKED)
const isReady = computed(() => status.value === REPORT_STATUS.READY)

// Какой платный разбор предлагать в конце бесплатного. Таблица, а не проверка
// имени: у карты и у пары пары типов свои, и «всё, что не full — free» сломает
// первый же новый тип с бэкенда
const PAID_AFTER = Object.freeze({
  free_natal: 'full_natal',
  pair_free: 'pair_full',
})

// Этим разбором чтение не заканчивается: за ним идёт платный
const isFree = computed(() => PAID_AFTER[type.value] !== undefined)
const fullReport = computed(() =>
  isFree.value ? api.value.report(token.value, PAID_AFTER[type.value]) : null,
)

// Мостик к платному: фраза перед плиткой, объясняющая, чего в прочитанном
// тексте нет. В промпте её держать нельзя — текст кешируется один на пару, а
// площадка в ключ кеша не входит, и фраза поехала бы и в VK (на этом уже
// обожглись с free_natal_v3; docs/astro-prompts-v2.md, раздел 8).
// Числа сверены с кодом: тизер получает 6 контактов (pair_free.TOP_ASPECTS),
// платный — 12 (synastry.TOP_LIMIT)
const BRIDGE = Object.freeze({
  pair_free:
    'Выше — шесть самых сильных контактов ваших карт. В полном разборе их вдвое больше, ' +
    'и каждый разобран отдельно.',
})

// Показываем только в браузере: в VK и Telegram оплата запрещена, и фраза
// про «полный разбор» там обещала бы то, чего на площадке нет
const bridgeText = computed(() =>
  platform.name === 'web' ? (BRIDGE[type.value] ?? null) : null,
)

// Куда ведёт «Назад»: с платного — к бесплатному, откуда на него и перешли,
// иначе к владельцу разбора (карта — главная, пара — её экран).
// router.back() тут не годится — по прямой ссылке из VK история пустая, и
// «назад» вышвырнуло бы человека из приложения
const backTo = computed(() => {
  // find ищет тип, после которого идёт наш: так работает и для карты, и для
  // пары, не зная их имён
  const freeType = Object.keys(PAID_AFTER).find((free) => PAID_AFTER[free] === type.value)
  if (freeType) {
    const name = scope.value === 'pair' ? 'pair-report' : 'report'
    return { name, params: { token: token.value, type: freeType } }
  }

  return scope.value === 'pair'
    ? { name: 'pair', params: { token: token.value } }
    : { name: 'home' }
})

/** Перечитать состояние разборов этого владельца — один круг ожидания. */
function refresh() {
  return api.value.reload(token.value)
}

// Генерация идёт: частый опрос. Таймаут в три минуты зашит в usePolling,
// timedOut он поднимет сам, когда ждать станет бессмысленно
const { timedOut } = usePolling(refresh, isRunning)

// Разбор заперт: человек ушёл платить на сайт и вернётся через минуты, а то и
// вовсе не вернётся. Опрос здесь редкий, и ценен не интервалом, а тем, что
// usePolling спрашивает сервер при возвращении в приложение.
// 30_000 — то же число, что 30000: подчёркивания в числах JS игнорирует,
// они только для читаемости (как в Python)
usePolling(refresh, isLocked, { intervalMs: 30_000, timeoutMs: 30 * 60 * 1000 })

// Тексты отказов подбираем по машинному коду, а не берём поле error с сервера:
// формулировки бэкенда меняются вместе с продуктом (docs/frontend-plan.md)
const ERROR_TEXT = Object.freeze({
  // Личный счётчик бесплатных разборов, а не суточный: «завтра» тут не
  // поможет. С 01.09.2026 счётчик выключен (FREE_NATAL_LIMIT=0), код не
  // приходит — ветку держим на случай, если лимит вернут
  [ERROR_CODE.LIMIT_EXHAUSTED]: 'Бесплатные разборы закончились.',
  [ERROR_CODE.BUDGET_EXCEEDED]: 'Сегодня разборов заказали слишком много. Попробуй завтра.',
  [ERROR_CODE.NEEDS_PURCHASE]: 'Этот разбор откроется после покупки.',
  [ERROR_CODE.NOT_SUPPORTED]: 'Такой разбор мы пока не делаем.',
  [ERROR_CODE.FAILED]: 'Текст не дописался до конца. Попробуй ещё раз.',
})

const errorText = computed(
  () => ERROR_TEXT[current.value?.error_code] ?? 'Не получилось получить разбор.',
)

// Повтор помогает только при обрыве самой генерации: лимит, бюджет и покупка
// от нажатия кнопки не изменятся (на бэкенде они и не пускают в повтор)
const canRetry = computed(() => current.value?.error_code === ERROR_CODE.FAILED)

// Своё состояние кнопки: request() бросает ошибку наружу — её ждёт человек,
// который только что нажал, и показать её, кроме экрана, некому
const starting = ref(false)
const startError = ref(null)

/** Заказать разбор. Ответ приходит со статусом running — дальше ведёт поллинг. */
async function start() {
  starting.value = true
  startError.value = null

  try {
    await api.value.request(token.value, type.value)
  } catch (failure) {
    startError.value = failure.message
  } finally {
    starting.value = false
  }
}

// С главной сюда приходят с уже загруженным списком (он общий на приложение) —
// грузим только при заходе по прямой ссылке. ensureLoaded от повторов защищён
watch(
  token,
  (value) => {
    if (value) api.value.ensureLoaded(value)
  },
  { immediate: true },
)
</script>

<template>
  <div class="flex flex-1 flex-col">
    <!-- Шапка одна на все состояния: чей это разбор и куда уйти назад.
         items-baseline ровняет строчки по нижней линии букв, а не по краю
         блока — с разными кеглями это единственный способ не «прыгать» -->
    <header class="mb-7 flex items-baseline justify-between gap-3">
      <p class="min-w-0 truncate font-sans text-label tracking-label text-gold-dim uppercase">
        {{ current?.title ?? 'Разбор' }}
      </p>
      <!-- RouterLink — переход внутри приложения без перезагрузки страницы;
           shrink-0 не даёт flex сжать ссылку под длинным названием слева -->
      <RouterLink
        :to="backTo"
        class="shrink-0 font-sans text-label tracking-label text-gold-dim uppercase"
      >
        ← Назад
      </RouterLink>
    </header>

    <!-- 1. Разбора в списке нет: либо список ещё едет, либо не доехал, либо
         сервер про такой тип не знает. Три разных случая, три разных текста -->
    <section
      v-if="!current"
      class="flex flex-1 flex-col items-center justify-center gap-4 text-center"
    >
      <template v-if="listStatus === 'error'">
        <h1 class="font-display text-title text-ink">Не удалось открыть разбор</h1>
        <div class="mt-2 w-full">
          <AppButton @click="refresh">Повторить</AppButton>
        </div>
      </template>

      <template v-else-if="listStatus === 'ready'">
        <h1 class="font-display text-title text-ink">Такого разбора пока нет</h1>
        <p class="font-read text-lead text-ink-muted">
          Возможно, он ещё готовится к запуску — загляни позже.
        </p>
      </template>

      <p v-else class="animate-pulse font-display text-title text-ink-muted">Открываем разбор…</p>
    </section>

    <!-- 2. Текст готов — ради этого экрана всё и затевалось -->
    <template v-else-if="isReady">
      <ReportSections :sections="current.sections ?? []" />

      <!-- Полный разбор предлагаем в конце бесплатного: человек уже прочитал
           текст и знает, за чем идёт дальше. Плитка сама решает, вести ли
           наружу (заперт) или внутрь (уже открыт) -->
      <div v-if="isFree && fullReport" class="mt-10">
        <!-- Мостик стоит над плиткой, а не внутри неё: плитка одна на карту и
             пару и показывается ещё на главной, где читать человеку нечего -->
        <p v-if="bridgeText" class="mb-4 font-read text-read text-ink-muted">{{ bridgeText }}</p>

        <FullReportTile :token="token" :report="fullReport" :scope="scope" />
      </div>

      <!-- Оценка текста: по ней сравниваются поколения промпта. Спрашиваем
           после того, как человек дочитал, — раньше оценивать нечего -->
      <div class="mt-10">
        <ReportFeedback :token="token" :type="type" />
      </div>

      <!-- Подписка на гороскоп дня. Место выбрано тем же доводом: соглашаются
           на письма от того, чей текст только что понравился. Вне VK блок
           не рисуется сам -->
      <div class="mt-8">
        <GroupCard />
      </div>
    </template>

    <!-- 3. Остальные состояния — короткий экран по центру: заголовок разбора
         и то, что сейчас можно сделать -->
    <section v-else class="flex flex-1 flex-col items-center justify-center gap-4 text-center">
      <!-- У запертого разбора заголовка тут нет: его название написано на
           плитке ниже, и вторая такая же строка читалась бы как ошибка -->
      <h1 v-if="!isLocked" class="font-display text-display text-ink">{{ current.title }}</h1>

      <!-- Разбора нет, ничего не идёт, платить не надо — можно просить -->
      <template v-if="status === REPORT_STATUS.AVAILABLE">
        <p class="font-read text-lead text-ink-muted">
          Астролог прочитает карту и напишет разбор. Это займёт около минуты.
        </p>

        <!-- Состав будущего текста: заголовки секций приходят из промпта -->
        <ul v-if="current.section_titles.length" class="space-y-1">
          <li
            v-for="title in current.section_titles"
            :key="title"
            class="font-display text-lead text-gold"
          >
            {{ title }}
          </li>
        </ul>

        <div class="mt-2 w-full">
          <AppButton :loading="starting" loading-text="Читаем карту…" @click="start">
            Получить разбор
          </AppButton>
        </div>

        <p v-if="startError" class="font-sans text-meta text-ink-muted">{{ startError }}</p>
      </template>

      <!-- Генерация идёт: опрос уже запущен, кнопки «обновить» не нужно, пока
           ждать не надоело самому поллингу -->
      <template v-else-if="isRunning">
        <p class="animate-pulse font-read text-lead text-ink-muted">
          Пишем разбор. Это занимает до минуты…
        </p>

        <template v-if="timedOut">
          <p class="font-sans text-meta text-ink-muted">
            Что-то затянулось. Разбор мог дописаться — проверь ещё раз.
          </p>
          <div class="mt-2 w-full">
            <AppButton @click="refresh">Обновить</AppButton>
          </div>
        </template>
      </template>

      <!-- Заперт: текста нет и не будет, пока разбор не откроют. Ту же плитку
           показывает конец бесплатного разбора — она умеет и состав секций
           перечислить, и увести на сайт, не называя это оплатой -->
      <div v-else-if="isLocked" class="w-full">
        <FullReportTile :token="token" :report="current" :scope="scope" />
      </div>

      <!-- Отказ: текст подобран по коду, а кнопка есть только там, где повтор
           вообще может помочь -->
      <template v-else>
        <p class="font-read text-lead text-ink-muted">{{ errorText }}</p>

        <div v-if="canRetry" class="mt-2 w-full">
          <AppButton :loading="starting" loading-text="Читаем карту…" @click="start">
            Попробовать снова
          </AppButton>
        </div>

        <p v-if="startError" class="font-sans text-meta text-ink-muted">{{ startError }}</p>
      </template>
    </section>
  </div>
</template>
