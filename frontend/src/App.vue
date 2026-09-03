<script setup>
// Корневой компонент: гейт входа, место под экраны роутера и нижняя навигация.
// Показывает его main.js. Данных снаружи не принимает: личность добывает сам
// через useSession, а что показать по текущему адресу — решает роутер.

import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { platform } from '@/app/platform'
import { useCharts } from '@/shared/composables/useCharts'
import { useSession } from '@/shared/composables/useSession'
import AppButton from '@/shared/ui/AppButton.vue'
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

// Есть ли куда отправить за входом. Метод login есть только у веб-платформы:
// в VK и Telegram личность приходит с запуском, предлагать там нечего.
// Обычная константа, не computed: платформа за время работы не меняется
const canLogin = typeof platform.login === 'function'

// Гейт закрыт по двум разным причинам, и слова у них разные: anon — войти
// нечем (это штатно), error — вход сорвался
const gateTitle = computed(() =>
  status.value === 'anon' ? 'Нужен вход' : 'Не получилось войти',
)

const gateText = computed(() => {
  // Текст ошибки готовит useSession; при anon его может не быть вовсе
  if (error.value) return error.value
  if (status.value !== 'anon') return null
  return canLogin
    ? 'Карты и разборы привязаны к вашей странице VK'
    : 'Приложение открыто не из VK'
})

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

    <!-- 2. Сессии нет. Либо войти нечем (anon: в браузере ещё не входили,
         в Mini App открыли не из VK), либо вход сорвался (error: подпись не
         сошлась, сеть отвалилась, сервер не настроен). Разметка общая,
         различаются подписи и набор кнопок -->
    <div
      v-else-if="status === 'anon' || status === 'error'"
      class="flex flex-1 flex-col items-center justify-center gap-4 text-center"
    >
      <!-- {{ }} — подстановка значения в текст, как f-строка в Python.
           У ref и computed в шаблоне .value не пишут: Vue разворачивает их сам -->
      <h1 class="font-display text-title text-ink">{{ gateTitle }}</h1>
      <p v-if="gateText" class="text-meta text-ink-muted">{{ gateText }}</p>

      <!-- Кнопки во всю ширину колонки, но не шире max-w-xs: AppButton
           тянется на весь родитель, а на широком экране это уродливо -->
      <div class="mt-2 flex w-full max-w-xs flex-col gap-3">
        <!-- Импортированный компонент и импортированный объект platform в
             шаблоне доступны сами: всё, что объявлено в script setup на
             верхнем уровне, видно разметке -->
        <AppButton v-if="canLogin" @click="platform.login()">Войти через VK</AppButton>

        <!-- Повтор предлагаем только после сорвавшегося входа: при anon
             повторять нечего, там просто нет параметров запуска -->
        <AppButton v-if="status === 'error'" @click="retry">Повторить</AppButton>
      </div>
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
