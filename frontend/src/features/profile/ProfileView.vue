<script setup>
// Профиль: карты человека и юридический блок. Показывает его роутер по адресу
// /profile, вход — четвёртый пункт нижней навигации.
// Данных снаружи не принимает: карты берёт из useCharts, имя — из useSession.
//
// Юридическая часть здесь потому, что во Vue нет подвала, который на стенде
// печатался под каждой страницей (web/templates/base.html): политика и
// контакты обязаны быть доступны с любого экрана — п. 2.4.1 правил VK.

import { computed } from 'vue'
import { useRouter } from 'vue-router'

import GroupCard from '@/features/community/GroupCard.vue'
import AddChartCard from '@/features/home/AddChartCard.vue'
import { useCharts } from '@/shared/composables/useCharts'
import { useSession } from '@/shared/composables/useSession'
import { openLegal, visibleLegalPages } from '@/shared/legal'

const router = useRouter()
const { charts, current, select } = useCharts()
const { user } = useSession()

// Какие документы показывать, решает платформа: в VK их два (прайс и оферта
// запрещены внутри Mini App), в вебе четыре. Считаем один раз, не computed —
// платформа за время работы приложения не меняется
const legalPages = visibleLegalPages()

// Имя человека, а не карты: его вводили при первом разборе. Пустое бывает
// (карту заводят и безымянной) — тогда обычный заголовок экрана
const title = computed(() => user.value?.name || 'Профиль')

/** Подпись карты: дата рождения и город. Формат тот же, что в переключателе
 *  на главной, — человек должен узнавать строку. */
function birthLine(chart) {
  // Сервер отдаёт дату строкой «1990-11-14T12:30:00»; new Date её разбирает,
  // Intl.DateTimeFormat печатает по-русски со склонением месяца
  const date = new Intl.DateTimeFormat('ru-RU', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  }).format(new Date(chart.birth_local))

  return chart.place_name ? `${date} · ${chart.place_name}` : date
}

/** Сделать карту текущей и уйти к ней на главную: читать в профиле нечего,
 *  а выбор карты имеет смысл только там, где её разборы. */
function choose(token) {
  select(token)
  router.push({ name: 'home' })
}
</script>

<template>
  <div class="flex flex-1 flex-col">
    <!-- Шапка: капс-лейбл раздела и имя, как на остальных экранах -->
    <header class="mb-8">
      <p class="font-sans text-label tracking-label text-gold-dim uppercase">Профиль</p>
      <h1 class="mt-2 truncate font-display text-display text-ink">{{ title }}</h1>
      <!-- Метка тестера: у него сняты лимиты генераций, и без подписи
           непонятно, почему у знакомого разборы кончились, а у него нет -->
      <p v-if="user?.is_tester" class="mt-2 font-sans text-meta text-gold-dim">
        Тестер · лимит бесплатных разборов снят
      </p>
    </header>

    <!-- Карты. Пустого состояния нет намеренно: без единой карты guard
         роутера уводит в онбординг, сюда с пустым списком не попасть -->
    <section>
      <h2 class="font-sans text-label tracking-label text-gold-dim uppercase">Мои карты</h2>

      <ul class="mt-4 space-y-2">
        <li v-for="chart in charts" :key="chart.token">
          <!-- Текущая карта отмечена золотом рамки и имени — тот же способ,
               что в переключателе на главной, галочек в макете нет -->
          <button
            type="button"
            class="block w-full cursor-pointer rounded-card border bg-bg-card px-5 py-4 text-left transition-colors active:bg-gold/5"
            :class="chart.token === current?.token ? 'border-line-gold' : 'border-line-dim'"
            @click="choose(chart.token)"
          >
            <p
              class="truncate font-display text-lead"
              :class="chart.token === current?.token ? 'text-gold' : 'text-ink'"
            >
              {{ chart.person_name || 'Без имени' }}
            </p>
            <p class="truncate font-sans text-meta text-ink-muted">{{ birthLine(chart) }}</p>
          </button>
        </li>
      </ul>

      <!-- Тот же вход за новой картой, что на главной: другого нет -->
      <div class="mt-3">
        <AddChartCard />
      </div>
    </section>

    <!-- Подписка на гороскоп дня. В профиле она постоянная: разбор читают
         один раз, а включить и выключить письма человек должен уметь всегда.
         Вне VK блок не рисуется — компонент решает это сам -->
    <div class="mt-10">
      <GroupCard />
    </div>

    <!-- Документы. Открываются наружу, а не роутером: это страницы сайта,
         которые отдаёт FastAPI, и внутри приложения их быть не должно -->
    <section class="mt-10">
      <h2 class="font-sans text-label tracking-label text-gold-dim uppercase">Документы</h2>

      <ul class="mt-2">
        <li v-for="page in legalPages" :key="page.path" class="border-b border-line-dim last:border-b-0">
          <button
            type="button"
            class="flex w-full cursor-pointer items-center justify-between gap-3 py-4 text-left"
            @click="openLegal(page.path)"
          >
            <span class="min-w-0 truncate font-display text-lead text-ink">{{ page.label }}</span>
            <!-- Стрелка наружу: показывает, что откроется новая вкладка,
                 а не экран приложения -->
            <span class="shrink-0 font-sans text-label text-gold-dim">↗</span>
          </button>
        </li>
      </ul>
    </section>

    <!-- Граница ответственности. Формулировка утвердительная («решения за
         тобой»), а не перечень «не является консультацией»: та же, что в
         подвале стенда (web/templates/base.html) -->
    <p class="mt-10 font-sans text-meta text-ink-muted">
      Разбор составлен по твоей натальной карте. Это повод подумать о себе —
      решения о здоровье, деньгах и отношениях остаются за тобой.
    </p>
  </div>
</template>
