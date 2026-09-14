// Пары пользователя: список, отдельная пара с расчётом синастрии, сборка пары
// и ссылка-приглашение для партнёра. Состояние объявлено на уровне модуля —
// модуль выполняется один раз, поэтому список во всём приложении общий
// (как useCharts и useSession). Ошибку наружу не бросает: её читают из error,
// а решения принимает экран.
//
// Отдельный composable, а не ветка useCharts: в тот смотрит guard роутера,
// решая «гнать ли в онбординг», и пары в этом решении не участвуют вовсе.

import { computed, ref } from 'vue'

import { createInvite, createPair, getPair, listPairs } from '@/shared/api/endpoints'

const pairs = ref([]) // [{token, kind, is_complete, created_at, person_a, person_b, invite_open}]
const status = ref('idle') // idle | loading | ready | error
const error = ref(null)

// Второй кеш модуля: пара вместе с расчётом, по одной записи на token.
// В списке calc нет — его отдаёт только GET /pairs/{token}, а без calc
// экрану пары нечего показать
const details = ref({}) // token -> {status, error, pair}
const pendingDetails = new Map() // token -> промис идущей загрузки

// Что отдаём про пару, которую ещё ни разу не спрашивали. Одна общая
// константа вместо нового объекта на каждый вызов: computed в компонентах
// сравнивает результат по ссылке и на новом объекте пересчитывался бы вечно
const UNKNOWN_DETAIL = Object.freeze({ status: 'idle', error: null, pair: null })

// Промис идущей загрузки списка: экран пар и плитка могут спросить его
// почти одновременно, и без этого ушло бы два одинаковых GET /pairs
let pending = null

/** Забрать список с сервера. Внутренняя, без защиты от повторов. */
async function fetchPairs() {
  status.value = 'loading'
  error.value = null

  try {
    // Сервер отдаёт свежие первыми; своего порядка не наводим
    pairs.value = await listPairs()
    status.value = 'ready'
  } catch (failure) {
    status.value = 'error'
    error.value = failure.message
    // Прежний список не чистим: обрыв сети не значит, что пар не стало
  } finally {
    pending = null
  }
}

/** Убедиться, что список загружен. Повторные вызовы бесплатны: готовый
 *  список не перечитывается, идущая загрузка ожидается той же. */
function ensureLoaded() {
  if (status.value === 'ready') return Promise.resolve()
  if (pending) return pending

  pending = fetchPairs()
  return pending
}

/** Перечитать список заново. Нужен чаще, чем у карт: пара, ждавшая партнёра,
 *  становится собранной без нашего участия — просто когда тот откроет ссылку. */
function reload() {
  pending = pending ?? fetchPairs()
  return pending
}

/** Положить пару в начало списка, заменив запись с тем же token.
 *  Замена, а не вставка: повтор того же состава сервер дедуплицирует и
 *  возвращает уже собранную пару. */
function remember(pair) {
  pairs.value = [pair, ...pairs.value.filter((item) => item.token !== pair.token)]
  status.value = 'ready'
}

/** Состояние одной пары с расчётом: {status, error, pair}. Про незнакомый
 *  token отвечает idle — «не спрашивали», а не «нет такой». */
function detail(token) {
  return details.value[token] ?? UNKNOWN_DETAIL
}

/** Забрать пару с расчётом. Внутренняя: повторами занимается ensureDetail. */
async function fetchDetail(token) {
  // Прежний расчёт держим на месте, пока грузится новый: экран не должен
  // моргать скелетоном при обновлении уже показанной пары
  const previous = detail(token).pair

  details.value[token] = { status: 'loading', error: null, pair: previous }

  try {
    details.value[token] = { status: 'ready', error: null, pair: await getPair(token) }
  } catch (failure) {
    details.value[token] = { status: 'error', error: failure.message, pair: previous }
  } finally {
    pendingDetails.delete(token)
  }
}

/** Убедиться, что пара с расчётом загружена: готовая не перечитывается,
 *  идущая загрузка ожидается той же. */
function ensureDetail(token) {
  if (detail(token).status === 'ready') return Promise.resolve()

  const inFlight = pendingDetails.get(token) ?? fetchDetail(token)
  pendingDetails.set(token, inFlight)
  return inFlight
}

/** Перечитать пару с расчётом — после прихода партнёра пара перестаёт быть
 *  незавершённой, и прежний ответ с calc = null устаревает. */
function reloadDetail(token) {
  const inFlight = pendingDetails.get(token) ?? fetchDetail(token)
  pendingDetails.set(token, inFlight)
  return inFlight
}

/** Собрать пару: {chart_a_token, chart_b_token?, kind, inviter_label?}.
 *  Возвращает пару и кладёт её в список. Ошибку бросает наружу — в отличие
 *  от загрузок выше, это действие человека, и ответа ждёт он. */
async function create(data) {
  const pair = await createPair(data)
  remember(pair)
  return pair
}

/** Выдать ссылку-приглашение: {invite_token, expires_at}. Тоже бросает.
 *
 *  Результат не кешируем: живое приглашение сервер не перевыпускает и на
 *  повторный запрос отдаёт тот же токен с тем же сроком — свой кеш был бы
 *  копией чужого без единой выгоды. */
async function invite(token) {
  return await createInvite(token)
}

/** Доступ к парам из компонентов. Возвращает то же состояние всем. */
export function usePairs() {
  return {
    pairs,
    status,
    error,
    // Пока список не загружен, ответ «пар нет» был бы враньём: пустой экран
    // «заведите первую пару» не должен мигать поверх уже заведённых
    isLoaded: computed(() => status.value === 'ready'),
    ensureLoaded,
    reload,
    remember,
    detail,
    ensureDetail,
    reloadDetail,
    create,
    invite,
  }
}
