<script setup>
// Онбординг: экран, который заводит первую карту. Показывает его роутер по
// адресу /onboarding, снаружи данных не принимает. Сами четыре шага рисует
// BirthWizard — здесь только то, что делать с собранными данными: отправить
// на сервер, запомнить карту и увести к цели.
//
// Цель приходит в ?next от guard роутера: человек открыл плитку меню
// сообщества («карта», «пара», «гороскоп»), а карты у него ещё нет. Без цели
// онбординг обычный и уводит на главную.

import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { goalRoute, parseGoal } from '@/app/entry'
import { createChart } from '@/shared/api/endpoints'
import BirthWizard from '@/shared/birth/BirthWizard.vue'
import { useCharts } from '@/shared/composables/useCharts'

const route = useRoute()
const router = useRouter()

// Цель входа из ?next. parseGoal пропускает только белый список: адрес с
// ?next=что-угодно может собрать кто угодно
const goal = computed(() => parseGoal(route.query.next))

// Подписи первого шага под цель: человек видит, ради чего вводит дату
const INTRO = Object.freeze({
  natal: {
    title: 'Начнём с даты рождения',
    hint: 'По ней посчитаем твою карту и сразу напишем разбор',
  },
  pair: {
    title: 'Сначала — твоя дата',
    hint: 'Партнёра добавим следующим шагом',
  },
  horoscope: {
    title: 'Узнаем твой знак',
    hint: 'И каждое утро — прогноз в личные сообщения',
  },
})

// Нет цели — пустой объект: intro.title будет undefined, и мастер покажет
// свои обычные тексты. INTRO[null] в JS не ошибка, а undefined
const intro = computed(() => INTRO[goal.value] ?? {})
// hasCharts — есть ли у человека хоть одна карта. От этого зависит, можно ли
// уйти с онбординга: первого пользователя отпускать некуда, guard роутера
// вернёт его сюда же
const { hasCharts, remember } = useCharts()

const submitting = ref(false)
const error = ref(null)

/** Отправить данные рождения и увести к цели. Ошибку показываем на месте:
 *  данные уже введены, терять их из-за обрыва сети нельзя.
 *  payload — готовое тело запроса, его собрал мастер. */
async function submit(payload) {
  submitting.value = true
  error.value = null

  try {
    const chart = await createChart(payload)

    // Кладём карту в общий список сразу: иначе guard роутера, увидев пустой
    // список, развернёт нас с главной обратно в онбординг
    remember(chart)
    // replace, а не push: возврат «назад» в мастер после создания карты
    // никому не нужен. Без цели goalRoute отдаёт главную
    await router.replace(goalRoute(goal.value, chart.token))
  } catch (failure) {
    // detail — текст от нашего API (например, «Дата рождения в будущем»),
    // message — то, что осталось от обрыва сети
    error.value = failure.detail || failure.message
  } finally {
    submitting.value = false
  }
}

// Сюда пришли из переключателя за второй картой и передумали. replace, а не
// push: брошенный мастер в истории никому не нужен
function cancel() {
  router.replace({ name: 'home' })
}
</script>

<template>
  <!-- @submit="submit" — слушаем событие мастера: он отдаёт готовое тело
       запроса, мы решаем, куда его девать. :can-cancel отдаёт мастеру право
       показать «Отмену» на первом шаге -->
  <BirthWizard
    :busy="submitting"
    :error="error"
    :can-cancel="hasCharts"
    :date-title="intro.title"
    :date-hint="intro.hint"
    @submit="submit"
    @cancel="cancel"
  />
</template>
