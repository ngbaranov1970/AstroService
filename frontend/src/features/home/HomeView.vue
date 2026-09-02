<script setup>
// Главная: приветствие, бейджи Солнце/Луна/Асцендент, карточка «Сегодня»
// и плитки разборов. Показывает её роутер по адресу /home — это экран по
// умолчанию, на него же ведёт guard всех, у кого карта уже есть.
// Данных снаружи не принимает: карту берёт из useCharts, разборы — из useReports.

import { computed, watch } from 'vue'

import GroupCard from '@/features/community/GroupCard.vue'
import FullReportTile from '@/features/report/FullReportTile.vue'
import { REPORT_STATUS } from '@/shared/api/endpoints'
import { useCharts } from '@/shared/composables/useCharts'
import { useReports } from '@/shared/composables/useReports'
import DashedCard from '@/shared/ui/DashedCard.vue'

import AddChartCard from './AddChartCard.vue'
import AstroBadges from './AstroBadges.vue'
import ChartSwitcher from './ChartSwitcher.vue'
import { buildHomeTiles, HOME_NOTE } from './homeTiles'
import ReportTile from './ReportTile.vue'
import TodayCard from './TodayCard.vue'

// current — карта, выбранная в переключателе (по умолчанию самая свежая)
const { current, detail, ensureDetail } = useCharts()
const { state: reportsState, report, ensureLoaded, reload: reloadReports } = useReports()

// Адрес карты, вокруг которого крутится весь экран. null означает «списка ещё
// нет» — до сюда такое почти не доходит, guard роутера ждёт список до перехода
const token = computed(() => current.value?.token ?? null)

// Расчёт карты: он приезжает отдельным запросом, в списке карт его нет
const natal = computed(() => (token.value ? detail(token.value).chart?.calc?.natal : null) ?? null)

// Состояние списка разборов: из него берём и плитки, и признак ошибки
const reports = computed(() => (token.value ? reportsState(token.value) : null))

// Плитки главной целиком считает homeTiles.js — здесь только показ.
// Пока ответа API нет, список пустой: плитки нарисуются со статусом «Скоро»
const tiles = computed(() =>
  token.value ? buildHomeTiles(token.value, reports.value?.items ?? []) : [],
)
/** Разложить плитки одной секции по формам: широкие столбцом, узкие сеткой 2×1. */
function byShape(list) {
  return {
    wide: list.filter((tile) => tile.shape !== 'narrow'),
    narrow: list.filter((tile) => tile.shape === 'narrow'),
  }
}

// Две секции главной: работающие разборы и анонсы. Признак soon считает
// homeTiles.js, здесь только раскладка
const ready = computed(() => byShape(tiles.value.filter((tile) => !tile.soon)))
const soon = computed(() => byShape(tiles.value.filter((tile) => tile.soon)))

// Полный разбор идёт мимо плиток главной (в reportTypes.js у него onHome:
// false): обычная плитка — просто ссылка на экран разбора, а здесь нужны цена,
// состав секций и развилка «оплата внутри приложения или на сайте». Всё это
// умеет FullReportTile, тот же компонент показывает конец бесплатного разбора.
// null — списка разборов ещё нет либо сервер такого типа не отдаёт
const fullReport = computed(() => report(token.value, 'full_natal'))

// Предлагаем платное только тому, кто дочитал бесплатное: до первого текста
// «полный разбор из шести секций» — обещание неизвестно чего
const freeIsReady = computed(() => report(token.value, 'free_natal')?.status === REPORT_STATUS.READY)

// Без точного времени асцендент считан от полудня — бейдж покажет причину
const timeKnown = computed(() => current.value?.time_known ?? true)

// Дата в шапке. Intl.DateTimeFormat — встроенное в браузер форматирование дат
// по языку, аналог strftime с локалью; вручную склеивать «суббота, 30 августа»
// значило бы держать у себя два списка склонений
const today = new Intl.DateTimeFormat('ru-RU', {
  weekday: 'long',
  day: 'numeric',
  month: 'long',
}).format(new Date())

// watch следит за значением и зовёт функцию при его смене. immediate — позвать
// сразу, не дожидаясь первой смены: карта обычно известна уже при открытии.
// Оба ensure* защищены от повторов внутри, лишних запросов не будет
watch(
  token,
  (value) => {
    if (!value) return
    ensureDetail(value)
    ensureLoaded(value)
  },
  { immediate: true },
)
</script>

<template>
  <!-- space-y-7 — вертикальные промежутки между секциями (~28 px из макета).
       Горизонтальные отступы и ширину колонки задаёт App.vue, здесь их нет -->
  <div class="space-y-7">
    <!-- Двух кругов из макета здесь нет: один означал переключатель карт — им
         стало само имя (ChartSwitcher), второй профиль — тот экран отложен, а
         вход в него есть в нижней навигации.
         Имя рисует ChartSwitcher, а не этот файл: заголовок и список карт —
         один элемент управления, разрывать их между файлами незачем -->
    <header class="min-w-0">
      <p class="font-sans text-label tracking-label text-gold-dim uppercase">{{ today }}</p>
      <ChartSwitcher class="mt-2" />
    </header>

    <AstroBadges :natal="natal" :time-known="timeKnown" />

    <!-- Работающие разборы идут первыми: ради них человек сюда и пришёл -->
    <section class="space-y-3">
      <h2 class="font-sans text-label tracking-label text-gold-dim uppercase">Твои разборы</h2>

      <!-- Список не загрузился: плитки ниже всё равно нарисованы (как анонсы),
           поэтому здесь нужна только строка с кнопкой «повторить» -->
      <p v-if="reports?.status === 'error'" class="font-sans text-meta text-ink-muted">
        Не удалось загрузить разборы.
        <button type="button" class="text-gold underline" @click="reloadReports(token)">
          Повторить
        </button>
      </p>

      <ReportTile v-for="tile in ready.wide" :key="tile.key" :tile="tile" />

      <div v-if="ready.narrow.length" class="grid grid-cols-2 gap-3">
        <ReportTile v-for="tile in ready.narrow" :key="tile.key" :tile="tile" />
      </div>

      <!-- Полный разбор — последним в секции: сначала то, что уже есть, потом
           платное предложение. Плитка сама решает, вести ли на оплату (заперт)
           или в текст (уже куплен) и показывать ли цену: в VK её нет -->
      <FullReportTile v-if="fullReport && freeIsReady" :token="token" :report="fullReport" />

      <!-- Приглашение завести вторую карту стоит сразу за разборами: рядом с
           готовым текстом понятно, что именно получит близкий. Внизу экрана,
           среди анонсов, оно читалось бы как ещё одно «скоро» -->
      <AddChartCard />
    </section>

    <!-- Тот же блок стоит в конце разбора и в профиле: там его видит тот, кто
         дочитал, здесь — тот, кто до разбора не дошёл. Показываться или нет,
         решает сам GroupCard (вне VK кнопок нет), состояние у всех экранов
         общее — оно живёт в useGroup -->
    <GroupCard />

    <!-- Анонсы: то, чего ещё нет. Отдельный заголовок вместо подписи «скоро»
         под каждой плиткой -->
    <section class="space-y-3">
      <h2 class="font-sans text-label tracking-label text-gold-dim uppercase">Скоро</h2>

      <!-- Транзитов в API нет — карточка дня стоит в состоянии-заглушке и
           лежит среди анонсов. Место в макете за ней сохраняется -->
      <TodayCard>Разбор дня появится здесь совсем скоро.</TodayCard>

      <ReportTile v-for="tile in soon.wide" :key="tile.key" :tile="tile" />

      <div v-if="soon.narrow.length" class="grid grid-cols-2 gap-3">
        <ReportTile v-for="tile in soon.narrow" :key="tile.key" :tile="tile" />
      </div>
    </section>

    <DashedCard>{{ HOME_NOTE }}</DashedCard>
  </div>
</template>
