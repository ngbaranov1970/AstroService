<script setup>
// Экран пары: либо пара ждёт партнёра по ссылке, либо она собрана и у неё
// есть разборы совместимости. Показывает его роутер по адресу /pair/:token.
// Данные берёт сам: usePairs — саму пару с расчётом, useReports('pair') —
// тизер и платный разбор.

import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { usePairs } from '@/shared/composables/usePairs'
import { usePolling } from '@/shared/composables/usePolling'
import { useReports } from '@/shared/composables/useReports'
import AppButton from '@/shared/ui/AppButton.vue'

const route = useRoute()
const { detail, ensureDetail, reloadDetail, invite } = usePairs()
// 'pair' — тот самый scope: те же state/ensureLoaded, но ходят в /pairs/…
const {
  state: reportsState,
  ensureLoaded: ensureReports,
  reload: reloadReports,
} = useReports('pair')

// Параметры роутера бывают и массивом (это его контракт), поэтому приводим
// к строке явно — она уходит в запросы
const token = computed(() => String(route.params.token ?? ''))

const pairState = computed(() => detail(token.value))
// null — пару ещё не спрашивали либо запрос не удался; различает это status
const pair = computed(() => pairState.value.pair)
const isComplete = computed(() => pair.value?.is_complete === true)

// Подписи типов связи. Текст короткий: это метка под заголовком, а не
// объяснение — выбор человек делал на экране сборки и помнит его
const KIND_LABEL = Object.freeze({
  romance: 'Романтическая связь',
  family: 'Семейная связь',
})

// Имена сторон подписаны не всегда: имя карты необязательно. Безымянную
// сторону называем по её роли, иначе заголовок превратится в «и»
const title = computed(() => {
  const a = pair.value?.person_a || 'Ты'
  const b = pair.value?.person_b || 'Партнёр'
  return `${a} и ${b}`
})

// Разборы этой пары. У пары без второй стороны список пуст — так отвечает
// сервер, и отдельной ветки «ещё нет» здесь не нужно
const reports = computed(() => reportsState(token.value).items)

// ─── Приглашение ───────────────────────────────────────────────────────────

const inviteUrl = ref(null)
const inviteError = ref(null)
// Показываем «Скопировано» несколько секунд: другого подтверждения у копии
// в буфер нет вовсе
const copied = ref(false)

/** Адрес экрана приглашения для партнёра.
 *
 *  Собираем из текущего адреса, а не из зашитого домена: приложение открыто
 *  то на своём домене, то внутри VK, и ссылка обязана вести туда же, откуда
 *  её отправляют. Хеш-часть своя — у роутера режим hash-истории. */
function buildInviteUrl(inviteToken) {
  const { origin, pathname } = window.location
  return `${origin}${pathname}#/invite/${inviteToken}`
}

/** Спросить у сервера ссылку-приглашение.
 *
 *  Зовётся сама при открытии экрана незавершённой пары: без ссылки такой
 *  экран бесполезен. Повтор безопасен — живое приглашение сервер не
 *  перевыпускает и отдаёт тот же токен. */
async function loadInvite() {
  inviteError.value = null

  try {
    const { invite_token: inviteToken } = await invite(token.value)
    inviteUrl.value = buildInviteUrl(inviteToken)
  } catch (failure) {
    // 409 означает «партнёр уже пришёл» — то есть пара у нас устарела;
    // перечитываем её, и экран сам перерисуется в собранный вид
    if (failure.status === 409) {
      reloadDetail(token.value)
      return
    }
    inviteError.value = failure.detail || failure.message
  }
}

/** Положить ссылку в буфер обмена. */
async function copyInvite() {
  try {
    // navigator.clipboard есть не везде: в вебвью без HTTPS его нет вовсе, а
    // в некоторых сборках он бросает отказ. Ссылка поэтому всегда видна на
    // экране текстом — её можно выделить руками
    await navigator.clipboard.writeText(inviteUrl.value)
    copied.value = true
    setTimeout(() => {
      copied.value = false
    }, 2000)
  } catch {
    inviteError.value = 'Скопировать не вышло — ссылку можно выделить и скопировать вручную'
  }
}

// ─── Загрузка и ожидание партнёра ──────────────────────────────────────────

/** Перечитать пару — один круг ожидания партнёра. */
function refresh() {
  return reloadDetail(token.value)
}

// Партнёр приходит без нашего участия, когда откроет ссылку: опрашиваем редко
// и ценно здесь не столько расписание, сколько то, что usePolling спрашивает
// сервер при возвращении в приложение — ссылку отправляют из мессенджера и
// возвращаются сюда. Полчаса — потолок: дальше человек всё равно перезайдёт
const waiting = computed(() => pair.value !== null && !isComplete.value)
usePolling(refresh, waiting, { intervalMs: 30_000, timeoutMs: 30 * 60 * 1000 })

// Ссылку просим ровно один раз за появление незавершённой пары: watch по
// waiting, а не вызов в момент загрузки, — пара приезжает асинхронно, и к
// первому кадру экрана её ещё нет
watch(waiting, (value) => {
  if (value && inviteUrl.value === null) loadInvite()
})

// Оба ensure* защищены от повторов внутри, лишних запросов не будет.
// Разборы грузим и для незавершённой пары: сервер отвечает пустым списком,
// а ветку «пара только что собралась» это избавляет от особого случая
watch(
  token,
  (value) => {
    if (!value) return
    ensureDetail(value)
    ensureReports(value)
  },
  { immediate: true },
)

// Пара собралась, пока мы смотрели на экран ожидания. Именно reload, а не
// ensureLoaded: у незавершённой пары список уже загружен и пуст (сервер
// отвечает пустым), и «убедиться, что загружен» не перечитал бы ничего —
// тизер не появился бы до перезахода на экран
watch(isComplete, (value) => {
  if (value) reloadReports(token.value)
})

// Подпись действия на плитке разбора. Та же таблица, что на главной: экран
// пары ничего не решает про оплату — этим занят экран разбора, как и у карты
const ACTION = Object.freeze({
  ready: 'Читать',
  running: 'Готовится',
  available: 'Получить разбор',
  locked: 'Открыть',
  failed: 'Попробовать снова',
})
</script>

<template>
  <div class="flex flex-1 flex-col">
    <!-- Шапка одна на все состояния: чья это пара и куда уйти назад -->
    <header class="mb-7 flex items-baseline justify-between gap-3">
      <p class="min-w-0 truncate font-sans text-label tracking-label text-gold-dim uppercase">
        Совместимость
      </p>
      <RouterLink
        :to="{ name: 'home' }"
        class="shrink-0 font-sans text-label tracking-label text-gold-dim uppercase"
      >
        ← Назад
      </RouterLink>
    </header>

    <!-- 1. Пары ещё нет. Загрузка и отказ различаются: во втором случае есть
         что предложить нажать -->
    <section
      v-if="!pair"
      class="flex flex-1 flex-col items-center justify-center gap-4 text-center"
    >
      <template v-if="pairState.status === 'error'">
        <h1 class="font-display text-title text-ink">Не удалось открыть пару</h1>
        <div class="mt-2 w-full">
          <AppButton @click="refresh">Повторить</AppButton>
        </div>
      </template>

      <p v-else class="animate-pulse font-display text-title text-ink-muted">Открываем пару…</p>
    </section>

    <!-- 2. Партнёра ещё нет: главное на экране — ссылка, остальное объясняет,
         что с ней делать -->
    <section v-else-if="!isComplete" class="flex flex-1 flex-col justify-center gap-5">
      <div class="text-center">
        <h1 class="font-display text-display text-ink">Ждём второго</h1>
        <p class="mt-2 font-read text-lead text-ink-muted">
          Отправь ссылку — партнёр введёт свои данные сам, и разбор появится здесь.
        </p>
      </div>

      <!-- Ссылка видна текстом всегда, а не только в буфере обмена:
           копирование в вебвью иногда молча недоступно. break-all разрешает
           перенос посреди слова — адрес иначе вылезает за край экрана -->
      <div v-if="inviteUrl" class="rounded-card border border-line-dim bg-bg-card p-4">
        <p class="font-sans text-meta break-all text-ink-muted">{{ inviteUrl }}</p>
      </div>

      <AppButton v-if="inviteUrl" @click="copyInvite">
        {{ copied ? 'Скопировано' : 'Скопировать ссылку' }}
      </AppButton>

      <p v-if="inviteError" class="text-center font-sans text-meta text-ink-muted">
        {{ inviteError }}
      </p>

      <!-- Предупреждение обязательное, а не вежливое: партнёр вводит свои
           данные, и они сохранятся в этом аккаунте (152-ФЗ) -->
      <p class="text-center font-sans text-meta text-ink-muted">
        Данные партнёра сохранятся в твоём аккаунте, а он увидит общий разбор.
      </p>
    </section>

    <!-- 3. Пара собрана: имена, тип связи и разборы -->
    <section v-else class="space-y-7">
      <header class="text-center">
        <h1 class="font-display text-display text-ink">{{ title }}</h1>
        <p class="mt-2 font-sans text-label tracking-label text-gold-dim uppercase">
          {{ KIND_LABEL[pair.kind] ?? 'Связь' }}
        </p>
      </header>

      <div class="space-y-3">
        <!-- Плитки строим прямо из ответа сервера, не из своего реестра типов:
             состав разборов пары решает бэкенд, и новый тип появится здесь
             без правки фронта -->
        <RouterLink
          v-for="item in reports"
          :key="item.type"
          :to="{ name: 'pair-report', params: { token, type: item.type } }"
          class="block rounded-card border border-line-dim bg-bg-card p-5 transition-colors active:bg-gold/5"
        >
          <p class="font-display text-title text-ink">{{ item.title }}</p>

          <p v-if="item.section_titles?.length" class="mt-3 font-sans text-meta text-ink-muted">
            {{ item.section_titles.join(' · ') }}
          </p>

          <p class="mt-4 font-sans text-label tracking-label text-gold uppercase">
            {{ ACTION[item.status] ?? 'Открыть' }} →
          </p>
        </RouterLink>

        <!-- Список пуст: расчёт есть, а разборов сервер не отдал. Это не
             норма, поэтому говорим прямо, а не рисуем пустоту -->
        <p v-if="!reports.length" class="text-center font-sans text-meta text-ink-muted">
          Разборы для этой пары пока недоступны.
        </p>
      </div>
    </section>
  </div>
</template>
