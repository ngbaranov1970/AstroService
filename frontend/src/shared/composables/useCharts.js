// Список карт пользователя: одна загрузка на запуск приложения и ответ на
// вопрос «карты вообще есть?». Состояние объявлено на уровне модуля — модуль
// выполняется один раз, поэтому список во всём приложении общий (как useSession).
// Ошибку наружу не бросает: её читают из error, а решения принимает экран.

import { computed, ref } from 'vue'

import { listCharts } from '@/shared/api/endpoints'

const charts = ref([]) // [{token, person_name, birth_local, time_known, place_name, created_at}]
const status = ref('idle') // idle | loading | ready | error
const error = ref(null)

// Промис идущей загрузки: guard и главная стартуют почти одновременно,
// и без этого ушло бы два одинаковых GET /charts (тот же приём, что в http.js)
let pending = null

/** Забрать список с сервера и разложить по местам. Внутренняя, без защиты
 *  от повторов — ею занимаются ensureLoaded и reload. */
async function fetchCharts() {
  status.value = 'loading'
  error.value = null

  try {
    // Сервер отдаёт свежие первыми; своего порядка не наводим
    charts.value = await listCharts()
    status.value = 'ready'
  } catch (failure) {
    status.value = 'error'
    error.value = failure.message
    // Прежний список не чистим: обрыв сети не значит, что карт не стало
  } finally {
    pending = null
  }
}

/** Убедиться, что список загружен. Повторные вызовы бесплатны: готовый
 *  список не перечитывается, идущая загрузка ожидается той же. */
function ensureLoaded() {
  if (status.value === 'ready') return Promise.resolve()
  if (pending) return pending

  pending = fetchCharts()
  return pending
}

/** Перечитать список заново — после создания карты или по кнопке «повторить». */
function reload() {
  pending = pending ?? fetchCharts()
  return pending
}

/** Положить карту, только что вернувшуюся из POST /charts, в начало списка.
 *  Дедупликация на сервере может вернуть уже известную карту — тогда старую
 *  запись заменяем, а не добавляем вторую с тем же token. */
function remember(chart) {
  // filter оставляет элементы, для которых функция вернула true — как
  // списковое включение [c for c in charts if c.token != chart.token]
  charts.value = [chart, ...charts.value.filter((item) => item.token !== chart.token)]
  status.value = 'ready'
}

/** Доступ к списку из компонентов и guard'а. Возвращает то же состояние всем. */
export function useCharts() {
  return {
    charts,
    status,
    error,
    // Пока список не загружен, ответ «карт нет» был бы враньём — отсюда
    // отдельная проверка на ready: guard по ней отличает «пусто» от «не знаем»
    isLoaded: computed(() => status.value === 'ready'),
    hasCharts: computed(() => charts.value.length > 0),
    // Карта по умолчанию — самая свежая: её показывает главная, пока
    // переключателя карт нет
    primary: computed(() => charts.value[0] ?? null),
    ensureLoaded,
    reload,
    remember,
  }
}
