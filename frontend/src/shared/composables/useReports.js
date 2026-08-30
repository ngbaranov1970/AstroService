// Разборы карты: что готово, что готовится, что закрыто. Состояние объявлено
// на уровне модуля, поэтому оно общее для всего приложения (как useSession и
// useCharts) — главная и экран разбора смотрят в один список.
// Ошибку наружу не бросает: её читают из state(token).error, решает экран.
//
// Здесь только хранение и запросы. Опрос «раз в три секунды, пока running»
// живёт в экране разбора: пауза при свёрнутом приложении и таймаут — его дело.

import { ref } from 'vue'

import { listReports, requestReport } from '@/shared/api/endpoints'

// token карты -> {status, error, items}. items — массив ReportOut в порядке,
// который прислал сервер (он же порядок supported_types() на бэкенде)
const byChart = ref({})
const pending = new Map() // token -> промис идущей загрузки

const UNKNOWN = Object.freeze({ status: 'idle', error: null, items: [] })

/** Состояние списка разборов одной карты: {status, error, items}.
 *  status: idle | loading | ready | error. */
function state(token) {
  return byChart.value[token] ?? UNKNOWN
}

/** Один разбор по типу ('free_natal', 'full_natal') или null, если список
 *  ещё не загружен либо сервер такого типа не отдаёт. */
function report(token, type) {
  // find возвращает первый подошедший элемент или undefined — как next((x for
  // x in items if ...), None) в Python
  return state(token).items.find((item) => item.type === type) ?? null
}

/** Забрать список с сервера. Внутренняя, без защиты от повторов. */
async function fetchReports(token) {
  // Прежние статусы держим на месте: поллинг зовёт эту функцию раз в
  // несколько секунд, и обнуление списка мигало бы плитками на каждом круге
  const previous = state(token).items

  byChart.value[token] = { status: 'loading', error: null, items: previous }

  try {
    byChart.value[token] = { status: 'ready', error: null, items: await listReports(token) }
  } catch (failure) {
    byChart.value[token] = { status: 'error', error: failure.message, items: previous }
  } finally {
    pending.delete(token)
  }
}

/** Убедиться, что список загружен: готовый не перечитывается, идущая
 *  загрузка ожидается той же. */
function ensureLoaded(token) {
  if (state(token).status === 'ready') return Promise.resolve()

  const inFlight = pending.get(token) ?? fetchReports(token)
  pending.set(token, inFlight)
  return inFlight
}

/** Перечитать заново — круг поллинга, возврат с оплаты, кнопка «повторить».
 *  Пока один запрос в пути, второго не будет. */
function reload(token) {
  const inFlight = pending.get(token) ?? fetchReports(token)
  pending.set(token, inFlight)
  return inFlight
}

/** Заменить в списке один разбор пришедшим с сервера. Список целиком не
 *  перечитываем: ответ POST — это уже актуальное состояние этого типа. */
function put(token, fresh) {
  const current = state(token)
  // map строит новый массив той же длины — как [f(x) for x in items].
  // Присваиваем новый массив, а не правим старый на месте: так значение
  // меняется целиком и подписчики точно пересчитаются
  const items = current.items.map((item) => (item.type === fresh.type ? fresh : item))

  byChart.value[token] = { ...current, items }
}

/** Попросить разбор. Возвращает пришедший ReportOut (обычно со статусом
 *  running) и кладёт его в список. Ошибку бросает наружу: кнопку нажал
 *  человек, и он ждёт ответа — экран ловит её сам. */
async function request(token, type) {
  const fresh = await requestReport(token, type)
  put(token, fresh)
  return fresh
}

/** Доступ к разборам из компонентов. Возвращает то же состояние всем. */
export function useReports() {
  return { state, report, ensureLoaded, reload, request }
}
