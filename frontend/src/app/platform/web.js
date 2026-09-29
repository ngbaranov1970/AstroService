// Платформа «обычный браузер»: сессию приносит сам адрес.
// После входа через VK ID бэкенд возвращает человека на /app/?s=<JWT>
// (backend/app/web/routes/vkid.py), обменивать уже нечего — токен готов.
// Вход по cookie (/auth/web) остался запасным и только для разработки:
// в проде эта ручка отвечает 404.

// Параметр с готовым токеном сессии и параметр «войти не удалось» —
// оба ставит vkid.py, других источников у них нет
const TOKEN_PARAM = 's'
const ERROR_PARAM = 'auth_error'

// Куда уводим за входом. Путь без домена: /app/ и /auth/vkid/start отдаёт
// один и тот же сервер, а на проде и в разработке домены разные
const LOGIN_PATH = '/auth/vkid/start'

// import.meta.env.DEV — флаг сборщика Vite: true при `npm run dev`, false в
// собранном приложении. Значение подставляется при сборке, поэтому ветка с
// cookie в боевой файл вообще не попадает
const isDev = import.meta.env.DEV

// Что сняли с адреса при старте. Читаем один раз в init(): дальше адресом
// распоряжается роутер, и вернуться к исходной строке уже нельзя
let tokenFromUrl = null
let authFailed = false

/** Убрать параметры входа из адресной строки, оставив путь и hash.
 *
 *  JWT в адресе иначе осядет в истории браузера, в закладке и в заголовке
 *  Referer при первом же переходе наружу — на оплату, например. */
function stripAuthParams(search) {
  search.delete(TOKEN_PARAM)
  search.delete(ERROR_PARAM)

  const query = search.toString()
  // Шаблонная строка (обратные кавычки) с подстановками ${...} — как f-строка
  // в Python. Тернарный оператор `условие ? да : нет` — как `да if условие else нет`
  const url = `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`

  // history.replaceState — подменить адрес в строке браузера, не перезагружая
  // страницу. Именно replace, а не push: новой записи в истории не появляется,
  // и кнопка «назад» не вернёт человека на адрес с токеном
  window.history.replaceState(null, '', url)
}

export const webPlatform = {
  name: 'web',

  /** Снять токен из адреса. Зовётся из main.js ДО монтирования Vue. */
  async init() {
    const search = new URLSearchParams(window.location.search)
    tokenFromUrl = search.get(TOKEN_PARAM)
    // has() отвечает «параметр есть», значение нам не важно
    authFailed = search.has(ERROR_PARAM)

    if (tokenFromUrl || authFailed) stripAuthParams(search)
  },

  /** Есть ли чем войти. Нет — гейт покажет кнопку «Войти через VK». */
  canAuth() {
    // Вернулись от VK ID с готовой сессией
    if (tokenFromUrl) return true
    // Попытка входа была и не удалась: молча подсунуть cookie-личность нельзя,
    // человек ждёт свою учётку, а не тестовую
    if (authFailed) return false
    return isDev
  },

  /** Почему войти нечем. null — человек просто ещё не входил, объяснять
   *  нечего; строка — попытка входа была и сорвалась (auth_error=1). */
  authNotice() {
    return authFailed ? 'Войти через VK не получилось' : null
  },

  /** Увести человека на вход VK ID.
   *
   *  Обычный переход по адресу, а не запрос: обмен кода требует, чтобы человек
   *  сам сходил на id.vk.ru и вернулся редиректом. Наличие этого метода —
   *  признак для гейта, что здесь есть что предложить кроме «Повторить». */
  login() {
    // assign — «перейти по адресу», как если бы нажали ссылку: страница
    // перезагружается, и наше приложение стартует заново уже с токеном
    window.location.assign(LOGIN_PATH)
  },

  /** Отдать сессию: {token, user}. Поле user пустое — имени и признака
   *  тестера в токене нет, а ручки «мой профиль» в API пока тоже нет.
   *  Бросает, когда токена больше нет: так же зовётся при 401 через сутки,
   *  и второй раз тот же токен отдавать нельзя — 401 пойдёт по кругу. */
  async obtainToken(http) {
    if (tokenFromUrl) {
      const token = tokenFromUrl
      // Токен одноразовый: он уже в http.js, а обновить его здесь нечем —
      // за новым человек идёт через VK ID заново
      tokenFromUrl = null
      return { token, user: null }
    }

    if (!isDev) {
      throw new Error('Сессия закончилась — войдите через VK заново')
    }

    // Разработка: личность едет в cookie, браузер прикладывает её к запросу
    // на свой же origin сам, поэтому тело пустое.
    // anonymous: true — заголовок с токеном этому запросу не нужен, и его
    // 401 не должен запускать перевыпуск: перевыпуск и есть этот запрос
    return await http.post('/auth/web', {}, { anonymous: true })
  },

  /** В браузере внешняя ссылка — обычная новая вкладка. */
  openExternal(url) {
    window.open(url, '_blank', 'noopener')
  },

  onBack() {
    return () => {}
  },

  /** Окон VK здесь нет — блок подписки на сообщество не рисуется вовсе.
   *  Методов joinGroup и allowMessages поэтому тоже нет: звать их некому. */
  supportsGroup() {
    return false
  },

  /** Поверх страницы в браузере ничего не лежит — обычный отступ сверху. */
  hasHeaderOverlay() {
    return false
  },

  async setHeaderColor() {},

  async close() {},
}
