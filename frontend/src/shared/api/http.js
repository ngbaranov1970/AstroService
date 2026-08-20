// Транспорт к нашему JSON-API: адрес, заголовки, разбор ответа и ошибки.
// Единственное место во фронте, где вызывается fetch. Токен сюда кладёт
// useSession, а обновляет его адаптер платформы (app/platform/*).

// Префикс версии стоит с первого дня: собранный Mini App живёт в кеше у VK,
// и старые сборки должны продолжать работать после смены формы ответов
const BASE = '/api/v1'

// Токен сессии (JWT на 24 ч). Держим в памяти модуля, в localStorage не
// пишем: в вебвью VK он живёт ровно один запуск, и хранить его негде и незачем
let token = null

// Функция «сходи за новым токеном» — её ставит useSession, а выполняет
// адаптер платформы. http.js сам про VK ничего не знает
let refresher = null

// Промис идущего обновления. Нужен, чтобы пять запросов, получивших 401
// одновременно, сходили за токеном ОДИН раз, а не пять (single-flight)
let refreshing = null

/** Ошибка HTTP: код ответа + текст из поля detail FastAPI.
 *  class ... extends Error — наследование, как в Python: наш класс это Error
 *  плюс два своих поля. Так `catch` может отличить «сервер ответил 404» от
 *  «сеть отвалилась» (там прилетит обычный TypeError). */
export class ApiError extends Error {
  constructor(status, detail, payload) {
    // super() — вызов конструктора родителя, как super().__init__() в Python
    super(detail || `HTTP ${status}`)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    // Полное тело ответа: у некоторых маршрутов там есть error_code
    this.payload = payload
  }
}

/** Положить токен сессии (или null, чтобы забыть). */
export function setToken(value) {
  token = value
}

/** Зарегистрировать способ добыть новый токен при 401.
 *  Ожидается async-функция, возвращающая строку токена. */
export function setTokenRefresher(fn) {
  refresher = fn
}

// Один поход в сеть, без всякой логики про 401. Вынесен отдельно, потому что
// request() зовёт его дважды: до обновления токена и после
async function send(method, path, { body, params, signal, anonymous } = {}) {
  // URLSearchParams сам экранирует значения: город «Ростов-на-Дону» в адресе
  // не развалится. Object.entries — как .items() у словаря в Python
  let url = BASE + path
  if (params) {
    const query = new URLSearchParams(
      // Пустые значения выбрасываем: ?q= бэкенду не нужен
      Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ''),
    ).toString()
    if (query) url += `?${query}`
  }

  const headers = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  // Заголовок ставим, только если токен есть и запрос не помечен анонимным
  if (token && !anonymous) headers.Authorization = `Bearer ${token}`

  // await — «дождись результата». fetch возвращает промис (обещание ответа),
  // это примерно то же, что await у httpx в асинхронном Python
  const response = await fetch(url, {
    method,
    headers,
    // signal — способ отменить запрос снаружи (уход со страницы, новый ввод в поиске)
    signal,
    // JSON.stringify — сериализация объекта в строку, как json.dumps
    body: body === undefined ? undefined : JSON.stringify(body),
  })

  // 204 No Content: тела нет, разбирать нечего
  const data = response.status === 204 ? null : await response.json().catch(() => null)

  // Главная ловушка fetch: он НЕ бросает исключение на 4xx/5xx — для него это
  // штатно доставленный ответ. Проверяем сами, иначе 401 поедет дальше как данные
  if (!response.ok) {
    // ?. — «обратись к полю, если объект не null»; иначе undefined вместо падения
    throw new ApiError(response.status, data?.detail, data)
  }

  return data
}

/** Запрос к API с одной попыткой перевыпустить токен при 401.
 *  Бросает ApiError на любой неуспешный ответ. */
export async function request(method, path, options = {}) {
  try {
    return await send(method, path, options)
  } catch (error) {
    // Обновляемся только на 401 и только если есть чем и есть что обновлять.
    // anonymous — это сам вход (/auth/vk): его 401 значит «подпись не сошлась»,
    // и повторять обмен бессмысленно, получится бесконечный круг
    const retriable =
      error instanceof ApiError && error.status === 401 && refresher && !options.anonymous
    if (!retriable) throw error

    // Первый пришедший запускает обновление, остальные ждут его же промис.
    // finally срабатывает и при успехе, и при ошибке — обнуляем, чтобы
    // следующий 401 через час снова смог сходить за токеном
    refreshing = refreshing ?? refresher().finally(() => { refreshing = null })
    await refreshing

    return await send(method, path, options)
  }
}

// Короткие имена для вызывающих: http.get('/charts'), http.post('/charts', {...}).
// Стрелочная функция (args) => выражение — это лямбда: короткая запись function
export const http = {
  get: (path, options) => request('GET', path, options),
  post: (path, body, options) => request('POST', path, { ...options, body }),
}
