// Разборы карты или пары: что готово, что готовится, что закрыто. Состояние
// объявлено на уровне модуля, поэтому оно общее для всего приложения (как
// useSession и useCharts) — главная и экран разбора смотрят в один список.
// Ошибку наружу не бросает: её читают из state(token).error, решает экран.
//
// Здесь только хранение и запросы. Опрос «раз в три секунды, пока running»
// живёт в экране разбора: пауза при свёрнутом приложении и таймаут — его дело.
//
// Кому принадлежит разбор — карте или паре, — задаётся один раз при вызове
// useReports('pair'); дальше экраны зовут те же state/report/request и про
// адрес ручки не знают.

import { ref } from 'vue'

import {
  listPairReports,
  listReports,
  requestPairReport,
  requestReport,
} from '@/shared/api/endpoints'

// Откуда брать разборы для каждого вида владельца. Форма ответа у обеих пар
// ручек одинаковая (ReportOut на бэкенде), поэтому всё, что ниже, различий
// между картой и парой уже не видит
const SOURCES = {
  chart: { list: listReports, request: requestReport },
  pair: { list: listPairReports, request: requestPairReport },
}

// 'chart:<token>' -> {status, error, items}. items — массив ReportOut в
// порядке, который прислал сервер (он же порядок supported_types() на бэкенде).
// Вид владельца в ключе обязателен: токены карт и пар выдаются независимо, и
// без него состояние пары легло бы поверх состояния карты
const byKey = ref({})
const pending = new Map() // тот же ключ -> промис идущей загрузки

const UNKNOWN = Object.freeze({ status: 'idle', error: null, items: [] })

/** Ключ хранилища. Шаблонная строка `${a}:${b}` — как f-строка в Python. */
function keyOf(scope, token) {
  return `${scope}:${token}`
}

/** Состояние списка разборов одного владельца: {status, error, items}.
 *  status: idle | loading | ready | error. */
function state(key) {
  return byKey.value[key] ?? UNKNOWN
}

/** Забрать список с сервера. Внутренняя, без защиты от повторов. */
async function fetchReports(scope, token) {
  const key = keyOf(scope, token)
  // Прежние статусы держим на месте: поллинг зовёт эту функцию раз в
  // несколько секунд, и обнуление списка мигало бы плитками на каждом круге
  const previous = state(key).items

  byKey.value[key] = { status: 'loading', error: null, items: previous }

  try {
    const items = await SOURCES[scope].list(token)
    byKey.value[key] = { status: 'ready', error: null, items }
  } catch (failure) {
    byKey.value[key] = { status: 'error', error: failure.message, items: previous }
  } finally {
    pending.delete(key)
  }
}

/** Запустить загрузку или дождаться уже идущей — общая часть ensureLoaded и
 *  reload. Пока один запрос в пути, второго не будет. */
function load(scope, token) {
  const key = keyOf(scope, token)
  const inFlight = pending.get(key) ?? fetchReports(scope, token)
  pending.set(key, inFlight)
  return inFlight
}

/** Заменить в списке один разбор пришедшим с сервера. Список целиком не
 *  перечитываем: ответ POST — это уже актуальное состояние этого типа. */
function put(key, fresh) {
  const current = state(key)
  // map строит новый массив той же длины — как [f(x) for x in items].
  // Присваиваем новый массив, а не правим старый на месте: так значение
  // меняется целиком и подписчики точно пересчитаются
  const items = current.items.map((item) => (item.type === fresh.type ? fresh : item))

  byKey.value[key] = { ...current, items }
}

/** Доступ к разборам из компонентов. Возвращает то же состояние всем, кто
 *  назвал тот же scope: 'chart' — разборы карты, 'pair' — разборы пары.
 *
 *  Значение по умолчанию оставлено ради пяти экранов, которые про пары не
 *  знают вовсе: они зовут useReports() и работают как раньше. */
export function useReports(scope = 'chart') {
  // Опечатка в имени scope иначе всплыла бы как «список всегда пустой» —
  // падение на месте дешевле
  if (!SOURCES[scope]) throw new Error(`useReports: неизвестный scope ${scope}`)

  // Ниже — замыкания: функции помнят scope, с которым их создали, и поэтому
  // вызывающему его передавать не нужно. В Python то же делает functools.partial
  return {
    /** Состояние списка разборов этого владельца. */
    state: (token) => state(keyOf(scope, token)),

    /** Один разбор по типу ('free_natal', 'pair_full') или null, если список
     *  ещё не загружен либо сервер такого типа не отдаёт. */
    report: (token, type) =>
      // find возвращает первый подошедший элемент или undefined — как next((x
      // for x in items if ...), None) в Python
      state(keyOf(scope, token)).items.find((item) => item.type === type) ?? null,

    /** Убедиться, что список загружен: готовый не перечитывается, идущая
     *  загрузка ожидается той же. */
    ensureLoaded: (token) =>
      state(keyOf(scope, token)).status === 'ready' ? Promise.resolve() : load(scope, token),

    /** Перечитать заново — круг поллинга, возврат с оплаты, кнопка «повторить». */
    reload: (token) => load(scope, token),

    /** Попросить разбор. Возвращает пришедший ReportOut (обычно со статусом
     *  running) и кладёт его в список. Ошибку бросает наружу: кнопку нажал
     *  человек, и он ждёт ответа — экран ловит её сам. */
    request: async (token, type) => {
      const fresh = await SOURCES[scope].request(token, type)
      put(keyOf(scope, token), fresh)
      return fresh
    },
  }
}
