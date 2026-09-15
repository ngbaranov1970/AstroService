<script setup>
// Экран приглашения: его открывает по ссылке партнёр, которого у нас нет в
// базе. Показывает роутер по адресу /invite/:token, данных снаружи не
// принимает. Единственный экран без нашей личности — у роута стоят оба флага
// (meta.public и meta.guest), и гейт в App.vue его пропускает.
//
// Ходит только в анонимные ручки /invites/*: авторизации у этого человека нет,
// и его 401 не должен запускать перевыпуск сессии (флаг anonymous в endpoints).

import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'

import ReportSections from '@/features/report/ReportSections.vue'
import { acceptInvite, readInvite, readInviteReport, REPORT_STATUS } from '@/shared/api/endpoints'
import BirthWizard from '@/shared/birth/BirthWizard.vue'
import { usePolling } from '@/shared/composables/usePolling'
import AppButton from '@/shared/ui/AppButton.vue'

import { kindLabel } from './pairKinds'

const route = useRoute()

// Токен приглашения из адреса. Он по смыслу пароль: в аналитику и в логи не
// идёт нигде, здесь живёт ровно столько, сколько открыт экран
const token = computed(() => String(route.params.token ?? ''))

// Состояние экрана целиком:
//   loading  — читаем приглашение
//   open     — приглашение живо, ждём данных партнёра
//   accepted — данные приняты (или ссылкой уже входили), показываем тизер
//   dead     — ссылки нет, она протухла или ею уже вошли и текста нет
//   error    — сеть или сервер, это лечится повтором
const stage = ref('loading')
const invite = ref(null) // {kind, inviter_label}
const teaser = ref(null) // ReportOut бесплатного разбора пары
const loadError = ref(null)

// Отправка данных партнёра. Отдельно от загрузки: её ждёт человек, который
// только что нажал «Готово»
const submitting = ref(false)
const submitError = ref(null)

// Открыт ли мастер ввода. Пока закрыт — человек читает, кто его зовёт и что
// станет с его данными: показать форму даты рождения раньше этого нельзя
// (152-ФЗ), да и вводить её незнакомому сервису никто не станет
const wizardOpen = ref(false)

// Кто зовёт. null в inviter_label — инициатор не подписался; безличное «тебя
// приглашают» честнее выдуманного имени
const inviterLabel = computed(() => invite.value?.inviter_label || null)

const teaserStatus = computed(() => teaser.value?.status ?? null)
const isRunning = computed(() => teaserStatus.value === REPORT_STATUS.RUNNING)
const isReady = computed(() => teaserStatus.value === REPORT_STATUS.READY)

/** Прочитать состояние тизера по той же ссылке.
 *
 *  Возвращает true, если текст (или его генерация) нашлись. Сервер сам ставит
 *  генерацию в очередь на этом GET — реестр генераций живёт в памяти процесса,
 *  и перезапуск между приёмом и первым опросом иначе оставил бы партнёра без
 *  текста навсегда. */
async function loadTeaser() {
  try {
    teaser.value = await readInviteReport(token.value)
    return true
  } catch {
    return false
  }
}

/** Прочитать приглашение и решить, что показывать.
 *
 *  Порядок запросов от частого к редкому: обычно по ссылке приходят впервые.
 *  404 на приглашении означает «нет, протухло или уже использовано» — сервер
 *  их намеренно не различает, и вторым запросом мы проверяем самый полезный
 *  из этих случаев: партнёр уже вводил данные и вернулся читать текст. */
async function load() {
  stage.value = 'loading'
  loadError.value = null

  try {
    invite.value = await readInvite(token.value)
    stage.value = 'open'
    return
  } catch (failure) {
    // Не 404 — это сеть или поломка сервера, и такое лечится повтором
    if (failure.status !== 404) {
      loadError.value = failure.detail || failure.message
      stage.value = 'error'
      return
    }
  }

  stage.value = (await loadTeaser()) ? 'accepted' : 'dead'
}

load()

/** Отправить данные партнёра: пара достраивается, тизер уходит в работу.
 *  payload — готовое тело запроса, его собрал мастер. */
async function submit(payload) {
  submitting.value = true
  submitError.value = null

  try {
    teaser.value = await acceptInvite(token.value, payload)
    stage.value = 'accepted'
  } catch (failure) {
    // 400 здесь означает «данные совпали с данными пригласившего» — ошибка
    // ввода, и человек её сам исправит, не уходя с мастера
    submitError.value = failure.detail || failure.message
  } finally {
    submitting.value = false
  }
}

/** Перечитать тизер — один круг ожидания генерации. */
function refresh() {
  return loadTeaser()
}

// Пока текст пишется, опрашиваем сервер обычным расписанием разбора: интервал
// и таймаут в три минуты зашиты в usePolling
const { timedOut } = usePolling(refresh, isRunning)
</script>

<template>
  <div class="flex flex-1 flex-col">
    <!-- 1. Читаем приглашение. Показать что-то осмысленное нельзя: мы ещё не
         знаем даже, живо ли оно -->
    <section v-if="stage === 'loading'" class="flex flex-1 items-center justify-center">
      <p class="animate-pulse font-display text-title text-ink-muted">Открываем приглашение…</p>
    </section>

    <!-- 2. Сеть или сервер. Отдельно от «ссылка не работает»: это лечится
         повтором, и кнопка здесь не обманывает -->
    <section
      v-else-if="stage === 'error'"
      class="flex flex-1 flex-col items-center justify-center gap-4 text-center"
    >
      <h1 class="font-display text-title text-ink">Не удалось открыть приглашение</h1>
      <p v-if="loadError" class="font-sans text-meta text-ink-muted">{{ loadError }}</p>
      <div class="mt-2 w-full max-w-xs">
        <AppButton @click="load">Повторить</AppButton>
      </div>
    </section>

    <!-- 3. Ссылки нет, она протухла или ею уже вошли. Причину не называем —
         сервер её и не сообщает, а гадать вслух хуже, чем не гадать -->
    <section
      v-else-if="stage === 'dead'"
      class="flex flex-1 flex-col items-center justify-center gap-4 text-center"
    >
      <h1 class="font-display text-display text-ink">Ссылка не работает</h1>
      <p class="font-read text-lead text-ink-muted">
        Возможно, приглашение устарело — попроси новое у того, кто его отправил.
      </p>

      <div class="mt-2 w-full max-w-xs">
        <RouterLink :to="{ name: 'home' }">
          <AppButton>Посчитать свою карту</AppButton>
        </RouterLink>
      </div>
    </section>

    <!-- 4. Приглашение живо. Сначала объясняем, кто зовёт и что будет с
         данными, и только потом показываем мастер -->
    <template v-else-if="stage === 'open'">
      <BirthWizard
        v-if="wizardOpen"
        :busy="submitting"
        :error="submitError"
        :can-cancel="true"
        submit-label="Готово"
        busy-label="Считаем совместимость…"
        name-title="Как тебя зовут?"
        name-hint="Имя появится в разборе — так видно, где чьё"
        @submit="submit"
        @cancel="wizardOpen = false"
      />

      <section v-else class="flex flex-1 flex-col justify-center gap-5">
        <div class="text-center">
          <!-- Имя зовущего — единственное, что мы про него отдаём наружу, и
               это отдельное поле, а не имя его карты -->
          <p class="font-sans text-label tracking-label text-gold-dim uppercase">
            {{ kindLabel(invite.kind) }}
          </p>
          <h1 class="mt-2 font-display text-display text-ink">
            {{ inviterLabel ? `${inviterLabel} зовёт` : 'Тебя приглашают' }}
          </h1>
          <p class="mt-3 font-read text-lead text-ink-muted">
            Сравним две карты и расскажем, что между вами происходит. Разбор бесплатный.
          </p>
        </div>

        <AppButton @click="wizardOpen = true">Ввести свои данные</AppButton>

        <!-- Предупреждение обязательное, а не вежливое: карта заводится в
             аккаунте пригласившего, своего у этого человека может не быть -->
        <p class="text-center font-sans text-meta text-ink-muted">
          Понадобятся дата, время и место рождения. Они сохранятся у того, кто тебя позвал, и он
          увидит общий разбор.
        </p>
      </section>
    </template>

    <!-- 5. Данные приняты: ждём текст, читаем текст или объясняем отказ -->
    <template v-else>
      <header class="mb-7 flex items-baseline justify-between gap-3">
        <p class="min-w-0 truncate font-sans text-label tracking-label text-gold-dim uppercase">
          {{ teaser?.title ?? 'Совместимость' }}
        </p>
      </header>

      <!-- Текст готов -->
      <template v-if="isReady">
        <ReportSections :sections="teaser.sections ?? []" />

        <!-- Виральный виток: у партнёра своей карты нет, а разбор он только
             что прочитал — это лучший момент предложить свою -->
        <div class="mt-10 rounded-card border border-line-gold bg-bg-card p-5 text-center">
          <p class="font-display text-title text-ink">А что в твоей карте?</p>
          <p class="mt-2 font-sans text-meta text-ink-muted">
            Бесплатный разбор по дате рождения — тот же, что видит тот, кто тебя позвал.
          </p>
          <div class="mt-5">
            <RouterLink :to="{ name: 'home' }">
              <AppButton>Посчитать свою карту</AppButton>
            </RouterLink>
          </div>
        </div>
      </template>

      <!-- Генерация идёт: опрос уже запущен, кнопка нужна только когда ждать
           надоело самому поллингу -->
      <section
        v-else-if="isRunning"
        class="flex flex-1 flex-col items-center justify-center gap-4 text-center"
      >
        <p class="animate-pulse font-read text-lead text-ink-muted">
          Считаем совместимость. Это занимает до минуты…
        </p>

        <template v-if="timedOut">
          <p class="font-sans text-meta text-ink-muted">
            Что-то затянулось. Разбор мог дописаться — проверь ещё раз.
          </p>
          <div class="mt-2 w-full max-w-xs">
            <AppButton @click="refresh">Обновить</AppButton>
          </div>
        </template>
      </section>

      <!-- Остальное — отказ. Кнопки повтора здесь нет намеренно: упавшую
           генерацию сервер по GET не перезапускает (иначе опрос раз в пять
           секунд платил бы за каждый круг), а приглашение после приёма
           закрыто. Заказать текст заново может только владелец пары -->
      <section v-else class="flex flex-1 flex-col items-center justify-center gap-4 text-center">
        <h1 class="font-display text-title text-ink">Разбор не дописался</h1>
        <p class="font-read text-lead text-ink-muted">
          Твои данные сохранились. Попроси того, кто тебя позвал, открыть разбор у себя — он
          запустится заново.
        </p>
      </section>
    </template>
  </div>
</template>
