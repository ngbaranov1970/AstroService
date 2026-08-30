// Карты пользователя: список (одна загрузка на запуск, ответ на вопрос «карты
// вообще есть?») и расчёт отдельной карты по token.
// Состояние объявлено на уровне модуля — модуль
// выполняется один раз, поэтому список во всём приложении общий (как useSession).
// Ошибку наружу не бросает: её читают из error, а решения принимает экран.

import { computed, ref } from 'vue'

import { getChart, listCharts } from '@/shared/api/endpoints'

const charts = ref([]) // [{token, person_name, birth_local, time_known, place_name, created_at}]
const status = ref('idle') // idle | loading | ready | error
const error = ref(null)

// Второй кеш модуля: карта вместе с расчётом, по одной записи на token.
// В списке выше calc нет — его отдаёт только GET /charts/{token}, а без
// calc.natal нечего показать ни бейджам главной, ни таблицам карты
const details = ref({}) // token -> {status, error, chart}
const pendingDetails = new Map() // token -> промис идущей загрузки

// Что отдаём про карту, которую ещё ни разу не спрашивали. Одна общая
// константа вместо создания объекта на каждый вызов: computed в компонентах
// сравнивает результат по ссылке и на новом объекте пересчитывался бы вечно
const UNKNOWN_DETAIL = Object.freeze({ status: 'idle', error: null, chart: null })

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

/** Состояние одной карты с расчётом: {status, error, chart}. Про незнакомый
 *  token отвечает idle — «не спрашивали», а не «нет такой». */
function detail(token) {
  // ?? — левое значение, если оно не null и не undefined, иначе правое
  return details.value[token] ?? UNKNOWN_DETAIL
}

/** Забрать карту с расчётом. Внутренняя: повторами занимается ensureDetail. */
async function fetchDetail(token) {
  // Прежний расчёт держим на месте, пока грузится новый: экран не должен
  // моргать скелетоном при обновлении уже показанной карты.
  // Присваивание по новому ключу Vue 3 замечает само (в Vue 2 требовался
  // Vue.set) — ref оборачивает объект в Proxy, следящий и за появлением полей
  const previous = detail(token).chart

  details.value[token] = { status: 'loading', error: null, chart: previous }

  try {
    details.value[token] = { status: 'ready', error: null, chart: await getChart(token) }
  } catch (failure) {
    details.value[token] = { status: 'error', error: failure.message, chart: previous }
  } finally {
    pendingDetails.delete(token)
  }
}

/** Убедиться, что карта с расчётом загружена. Повторные вызовы бесплатны:
 *  готовая не перечитывается, идущая загрузка ожидается той же. */
function ensureDetail(token) {
  if (detail(token).status === 'ready') return Promise.resolve()

  // Главная и BottomNav спрашивают карту почти одновременно — без общей
  // Map ушло бы два одинаковых GET (тот же приём, что у pending выше)
  const pending = pendingDetails.get(token) ?? fetchDetail(token)
  pendingDetails.set(token, pending)
  return pending
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
    detail,
    ensureDetail,
  }
}
