<script setup>
// Экран «Гороскоп в личку» — цель плитки меню сообщества (#horoscope).
// Показывает его роутер по адресу /horoscope, снаружи данных не принимает.
// Кнопки подписки и разрешения не свои: их рисует GroupCard, здесь только
// объяснение, зачем нажимать, и выход к бесплатному разбору.

import { computed, onMounted } from 'vue'

import { goalRoute } from '@/app/entry'
import GroupCard from '@/features/community/GroupCard.vue'
import { useCharts } from '@/shared/composables/useCharts'
import { useGroup } from '@/shared/composables/useGroup'

const { current } = useCharts()
// Те же status и available, что читает GroupCard: состояние общее на
// приложение, второго запроса к серверу не будет
const { status, available, ensureLoaded } = useGroup()

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

    <h1 class="font-display text-display text-ink">Твой гороскоп на каждый день</h1>
    <p class="mt-2 font-read text-lead text-ink-muted">
      Одно сообщение утром в личные сообщения ВКонтакте. Без спама — отключить можно в любой
      момент этой же кнопкой.
    </p>

    <!-- Подписка и разрешение писать. Блок сам решает, какие кнопки нужны
         этому человеку, и сам прячется, если нажимать их здесь нечем -->
    <GroupCard class="mt-8" />

    <p v-if="unavailable" class="mt-8 font-sans text-meta text-ink-muted">
      Включить рассылку можно только из приложения внутри ВКонтакте — открой его из нашего
      сообщества.
    </p>

    <!-- Выход к бесплатному разбору. v-if: без карты вести некуда (до сюда
         такое почти не доходит — guard роутера отправит в онбординг) -->
    <div v-if="current" class="mt-auto pt-8 text-center">
      <RouterLink
        :to="natalRoute"
        class="font-sans text-label tracking-label text-gold uppercase underline-offset-4 active:underline"
      >
        А пока — твоя карта рождения →
      </RouterLink>
    </div>
  </div>
</template>
