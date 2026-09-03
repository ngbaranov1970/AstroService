// Сессия приложения: обмен параметров запуска на токен и состояние входа.
// Единственное место, где зовётся platform.obtainToken и заполняется токен
// в http.js. Состояние объявлено на уровне модуля — модуль выполняется один
// раз, поэтому сессия во всём приложении одна (аналог глобального объекта).

import { computed, ref } from 'vue'

import { platform } from '@/app/platform'
import { ApiError, http, setToken, setTokenRefresher } from '@/shared/api/http'

// ref — «коробочка» со значением, за которой следит Vue: меняем .value, и все
// шаблоны, читающие эту переменную, перерисовываются сами
// anon — войти нечем: в браузере человек ещё не входил через VK ID, в Mini App
// приложение открыли не из VK. Отдельно от error, потому что чинится это не
// повтором запроса, а входом
const status = ref('idle') // idle | loading | ready | anon | error
const user = ref(null) // {name, is_tester} — то, что вернул /auth/vk
const error = ref(null) // фраза для человека, когда status === 'error'

// Промис идущего входа: два экрана, стартовавшие одновременно, ждут один
// обмен, а не заводят два (тот же приём single-flight, что в http.js)
let pending = null

/** Сходить за токеном и разложить ответ по местам. Возвращает токен —
 *  в таком виде функция годится и как refresher для http.js. */
async function issueToken() {
  const session = await platform.obtainToken(http)
  setToken(session.token)
  user.value = session.user
  return session.token
}

// Регистрируем перевыпуск сразу при загрузке модуля: 401 может прилететь
// в любой момент (токен живёт 24 ч), и ждать вызова useSession() незачем
setTokenRefresher(issueToken)

/** Перевести ошибку входа в текст для экрана-гейта. */
function describe(failure) {
  if (failure instanceof ApiError) {
    // 401 — подпись не сошлась: чаще всего адрес открыли не из VK или
    // параметры запуска протухли; 503 — на сервере не заполнен VK_SECURE_KEY
    if (failure.status === 401) return 'VK не подтвердил запуск приложения'
    if (failure.status === 503) return 'Вход из VK на сервере не настроен'
    return failure.detail || `Сервер ответил ошибкой ${failure.status}`
  }
  // Сюда попадает и отказ платформы («открыто не из VK»), и обрыв сети:
  // у обычной Error есть только message
  return failure.message || 'Не удалось связаться с сервером'
}

/** Начать (или повторить) вход. Не бросает: результат читается из status. */
async function start() {
  if (status.value === 'ready') return
  // Вход уже идёт — ждём его же, второго обмена не устраиваем
  if (pending) return pending

  status.value = 'loading'
  error.value = null

  // Платформе нечем авторизовать — не ошибка сети, а честное «здесь войти
  // нечем»: отдельная ветка, чтобы не ловить это исключением
  if (!platform.canAuth()) {
    status.value = 'anon'
    // authNotice — необязательный метод платформы: пояснение, почему войти
    // нечем (например «вход не удался»). ?.() зовёт метод, только если он есть,
    // ?? подставляет правое значение, когда левое null или undefined
    error.value = platform.authNotice?.() ?? null
    // Токен мог остаться от прошлой сессии — забываем: иначе запросы уйдут
    // с чужим или протухшим и получат 401 по кругу
    setToken(null)
    return
  }

  pending = issueToken()
    .then(() => {
      status.value = 'ready'
    })
    .catch((failure) => {
      status.value = 'error'
      error.value = describe(failure)
      // Токен мог остаться от прошлой удачной сессии — забываем, иначе
      // запросы пойдут с протухшим и получат 401 по кругу
      setToken(null)
    })
    // finally выполняется и после успеха, и после ошибки
    .finally(() => {
      pending = null
    })

  return pending
}

/** Доступ к сессии из компонентов. Возвращает то же состояние всем. */
export function useSession() {
  return {
    // computed — производное значение, пересчитывается само при смене status
    isReady: computed(() => status.value === 'ready'),
    isLoading: computed(() => status.value === 'loading'),
    status,
    user,
    error,
    start,
  }
}
