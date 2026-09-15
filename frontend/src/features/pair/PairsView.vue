<script setup>
// Экран списка пар: все пары этого человека и вход в сборку новой. Показывает
// его роутер по адресу /pairs; сюда ведут вкладка «Пара» в нижней навигации и
// плитка «Совместимость» на главной. Данные берёт сам из usePairs, снаружи
// ничего не принимает.

import { usePairs } from '@/shared/composables/usePairs'
import AppButton from '@/shared/ui/AppButton.vue'
import { kindLabel } from './pairKinds'

const { pairs, status, error, reload } = usePairs()

// Перечитываем при каждом заходе, а не ensureLoaded: пара, ждавшая партнёра,
// становится собранной без нашего участия — просто когда тот откроет ссылку.
// Список «как в прошлый раз» соврал бы ровно в тот момент, ради которого
// человек сюда и зашёл
reload()

/** Подпись пары. Безымянную сторону называем ролью, иначе от заголовка
 *  остаётся одно «и». */
function title(pair) {
  return `${pair.person_a || 'Ты'} и ${pair.person_b || 'Партнёр'}`
}
</script>

<template>
  <div class="flex flex-1 flex-col">
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

    <!-- 1. Список ещё не приезжал. Пустой экран здесь хуже надписи: пар может
         быть много, и «заведите первую» поверх них мигать не должен -->
    <section
      v-if="!pairs.length && status === 'loading'"
      class="flex flex-1 items-center justify-center"
    >
      <p class="animate-pulse font-display text-title text-ink-muted">Открываем пары…</p>
    </section>

    <!-- 2. Не загрузилось. Прежний список при обрыве сети остаётся на месте
         (так устроен usePairs), поэтому отказ показываем только на пустом -->
    <section
      v-else-if="!pairs.length && status === 'error'"
      class="flex flex-1 flex-col items-center justify-center gap-4 text-center"
    >
      <h1 class="font-display text-title text-ink">Не удалось открыть пары</h1>
      <p class="font-sans text-meta text-ink-muted">{{ error }}</p>
      <div class="mt-2 w-full">
        <AppButton @click="reload">Повторить</AppButton>
      </div>
    </section>

    <!-- 3. Пар нет вовсе: экран целиком про то, что здесь делают -->
    <section v-else-if="!pairs.length" class="flex flex-1 flex-col justify-center gap-5">
      <div class="text-center">
        <h1 class="font-display text-display text-ink">Пары пока нет</h1>
        <p class="mt-2 font-read text-lead text-ink-muted">
          Сравним твою карту с картой партнёра и расскажем, что между вами происходит.
        </p>
      </div>

      <RouterLink :to="{ name: 'pair-create' }">
        <AppButton>Собрать пару</AppButton>
      </RouterLink>
    </section>

    <!-- 4. Пары есть -->
    <section v-else class="space-y-3">
      <RouterLink
        v-for="pair in pairs"
        :key="pair.token"
        :to="{ name: 'pair', params: { token: pair.token } }"
        class="block rounded-card border border-line-dim bg-bg-card p-5 transition-colors active:bg-gold/5"
      >
        <p class="truncate font-display text-title text-ink">{{ title(pair) }}</p>

        <!-- У незавершённой пары состояние важнее типа связи: человек зашёл
             сюда посмотреть, пришёл ли партнёр -->
        <p class="mt-2 font-sans text-meta text-ink-muted">
          {{ pair.is_complete ? kindLabel(pair.kind) : 'Ждём партнёра' }}
        </p>
      </RouterLink>

      <!-- Кнопка под списком, а не над ним: список — то, зачем сюда заходят
           чаще всего -->
      <div class="pt-5">
        <RouterLink :to="{ name: 'pair-create' }">
          <AppButton>Собрать ещё одну</AppButton>
        </RouterLink>
      </div>
    </section>
  </div>
</template>
