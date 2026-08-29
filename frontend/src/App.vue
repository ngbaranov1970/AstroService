<script setup>
// Корневой компонент: гейт входа, место под экраны роутера и нижняя навигация.
// Показывает его main.js. Данных снаружи не принимает: личность добывает сам
// через useSession, а что показать по текущему адресу — решает роутер.

import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useCharts } from '@/shared/composables/useCharts'
import { useSession } from '@/shared/composables/useSession'
import BottomNav from '@/shared/ui/BottomNav.vue'

// Деструктуризация: забираем из возвращённого объекта нужные поля по именам —
// то же самое, что a, b = d['a'], d['b'] в Python, только короче
const { status, error, start } = useSession()
const { reload: reloadCharts } = useCharts()

// useRouter() — сам роутер, им делают переходы; useRoute() — текущий адрес
const router = useRouter()
const route = useRoute()

// idle показываем так же, как loading. App монтируется раньше, чем роутер
// довёл первый переход до конца, и без этой ветки на кадр мелькнул бы пустой
// RouterView
const isBooting = computed(() => status.value === 'idle' || status.value === 'loading')

// На онбординге навигации нет: оттуда ведёт только один путь — вперёд
const showNav = computed(() => route.name !== 'onboarding')

// Вход запускаем отсюда, а не полагаемся на guard роутера: у онбординга в
// guard ранний выход, и заход прямо на /onboarding остался бы без сессии.
// Повторный вызов из guard бесплатен — start() отдаёт тот же промис
onMounted(start)

/** Повторить вход после ошибки и переиграть текущий переход, если удалось. */
async function retry() {
  // start() не бросает исключений: результат читается из status
  await start()
  if (status.value !== 'ready') return

  // Список карт мог остаться в состоянии ошибки от прошлой попытки — сам он
  // не перечитается, а guard по нему решает, гнать ли в онбординг
  await reloadCharts()

  // Пока входа не было, guard пропустил переход, не заглядывая в карты.
  // Повторяем навигацию по тому же адресу: force заставляет роутер прогнать
  // guard заново, без него переход «туда, где мы и стоим» игнорируется
  await router.replace({
    name: route.name ?? 'home',
    params: route.params,
    query: route.query,
    force: true,
  })
}
</script>

<template>
  <!-- Колонка на всю высоту окна, шириной max-w-app и по центру: на широком
       экране контент и нижняя панель совпадают по ширине.
       Нижний отступ увеличен, когда панель видна: она fixed, то есть лежит
       поверх страницы, и без запаса накрыла бы конец экрана -->
  <div
    class="mx-auto flex min-h-dvh w-full max-w-app flex-col px-5 pt-6"
    :class="showNav ? 'pb-24' : 'pb-6'"
  >
    <!-- Три состояния гейта. v-if / v-else-if / v-else — обычное ветвление,
         только выбирает не значение, а кусок разметки: невыбранных в странице
         нет вовсе (это не «спрятать через display:none») -->

    <!-- 1. Вход идёт. Ничего осмысленного показать нельзя: карт мы ещё не
         видели, и рисовать пустые экраны — врать пользователю -->
    <div v-if="isBooting" class="flex flex-1 items-center justify-center">
      <p class="animate-pulse font-display text-title text-ink-muted">Соединяемся…</p>
    </div>

    <!-- 2. Вход не удался: подпись не сошлась, сеть отвалилась или на сервере
         не настроен вход из VK. Текст готовит useSession, здесь только показ -->
    <div
      v-else-if="status === 'error'"
      class="flex flex-1 flex-col items-center justify-center gap-4 text-center"
    >
      <h1 class="font-display text-title text-ink">Не получилось войти</h1>
      <!-- {{ }} — подстановка значения в текст, как f-строка в Python.
           У ref в шаблоне .value не пишут: Vue разворачивает его сам -->
      <p class="text-meta text-ink-muted">{{ error }}</p>

      <!-- Кнопка-заглушка: на шаге вёрстки станет компонентом AppButton
           варианта outline (золотая рамка, капс, во всю ширину) -->
      <button
        class="mt-2 rounded-card border border-line-gold px-6 py-3 font-sans text-label tracking-label text-gold uppercase"
        @click="retry"
      >
        Повторить
      </button>
    </div>

    <!-- 3. Вход прошёл — дальше командует роутер. RouterView — «дырка», в
         которую он вставляет компонент экрана, подходящий текущему адресу -->
    <RouterView v-else />
  </div>

  <!-- Панель вне колонки: она fixed и позиционируется от окна, а не от
       родителя. Прячем её на онбординге и пока экранов ещё нет.
       Двух корневых элементов в шаблоне Vue 3 не боится — обёртка ради
       одного корня, как в Vue 2, больше не нужна -->
  <BottomNav v-if="status === 'ready' && showNav" />
</template>
