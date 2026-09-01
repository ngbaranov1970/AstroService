// Роутер: таблица адресов приложения и правило «нет карты — только онбординг».
// Его подключает App.vue, экраны берутся лениво из features/*.
// Единственное место, где решается, что показать по данному адресу.

import { createRouter, createWebHashHistory } from 'vue-router'

import { useCharts } from '@/shared/composables/useCharts'
import { useSession } from '@/shared/composables/useSession'

// component: () => import(...) — ленивая загрузка: код экрана уезжает в
// отдельный файл сборки и качается при первом заходе на адрес, а не сразу.
// В Python аналога нет — там импорт всегда синхронный
const routes = [
  // Пустой адрес и любой мусор ведём на главную: гейт и guard разберутся,
  // пускать ли туда вообще
  { path: '/', redirect: '/home' },
  {
    path: '/onboarding',
    name: 'onboarding',
    component: () => import('@/features/onboarding/OnboardingView.vue'),
  },
  {
    path: '/home',
    name: 'home',
    component: () => import('@/features/home/HomeView.vue'),
  },
  {
    path: '/chart/:token',
    name: 'chart',
    component: () => import('@/features/chart/ChartView.vue'),
  },
  {
    path: '/report/:token/:type',
    name: 'report',
    component: () => import('@/features/report/ReportView.vue'),
  },
  {
    path: '/profile',
    name: 'profile',
    component: () => import('@/features/profile/ProfileView.vue'),
  },
  // Витрина оплаты и возврат с неё. Экраны веб-ветки: в VK продавать нельзя,
  // и пришедшего оттуда PayView сам уводит на разбор
  {
    path: '/pay/:token',
    name: 'pay',
    component: () => import('@/features/pay/PayView.vue'),
  },
  // Сюда редиректом возвращает серверная страница /pay/<token>/done, номер
  // заказа приходит в ?order
  {
    path: '/paid/:token',
    name: 'paid',
    component: () => import('@/features/pay/PaidView.vue'),
  },
  {
    path: '/soon/:feature',
    name: 'soon',
    component: () => import('@/features/soon/SoonView.vue'),
  },
  // :rest(.*) — «поймать всё остальное»: адрес с опечаткой не должен
  // оставлять пустой экран
  { path: '/:rest(.*)', redirect: '/home' },
]

export const router = createRouter({
  // Hash-режим: адрес выглядит как /app/?vk_user_id=…#/home. Параметры
  // запуска VK лежат в ?…, и переходы по хешу их не трогают — иначе первая
  // же навигация стёрла бы подпись, которой мы входим. Базу не передаём:
  // без аргумента роутер берёт текущий путь, а он у нас /app/
  history: createWebHashHistory(),
  routes,
  // При смене экрана прокручиваем к началу: без этого длинный разбор
  // открывается с середины предыдущей страницы
  scrollBehavior: () => ({ top: 0 }),
})

// beforeEach — проверка перед каждым переходом. Возврат true пропускает,
// объект с именем роута перенаправляет
router.beforeEach(async (to) => {
  // Онбординг открыт всегда: именно на нём заводят первую карту
  if (to.name === 'onboarding') return true

  const { status, start } = useSession()
  // start() безопасно звать повторно: готовая сессия возвращается сразу,
  // идущая — ожидается тем же промисом
  await start()
  // Вход не удался — пропускаем, экран всё равно не покажется: App.vue
  // рисует поверх роутера гейт с текстом ошибки и кнопкой «повторить»
  if (status.value !== 'ready') return true

  const { hasCharts, isLoaded, ensureLoaded } = useCharts()
  await ensureLoaded()
  // Список не загрузился (обрыв сети) — в онбординг не гоним: предлагать
  // завести карту заново тому, у кого она есть, хуже пустого экрана.
  // isLoaded отличает «карт нет» от «мы их не видели»
  if (isLoaded.value && !hasCharts.value) return { name: 'onboarding' }

  return true
})
