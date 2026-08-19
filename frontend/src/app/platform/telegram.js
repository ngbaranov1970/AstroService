// Заглушка платформы Telegram Web App: контракт есть, авторизации нет.
// Клиент Telegram сам кладёт в страницу объект window.Telegram.WebApp —
// отдельную библиотеку, как vk-bridge, ставить не нужно.

/** Объект, который вставляет клиент Telegram; undefined везде, кроме него. */
function tg() {
  // ?. по цепочке: если window.Telegram нет, вернётся undefined, а не ошибка
  return window.Telegram?.WebApp
}

export const telegramPlatform = {
  name: 'telegram',

  async init() {
    // ready() — тот же смысл, что VKWebAppInit: «загрузился, убирай заставку»
    tg()?.ready()
    tg()?.expand()
  },

  canAuth() {
    // initData — подписанная строка с личностью, аналог sign у VK
    return Boolean(tg()?.initData)
  },

  async obtainToken() {
    // Появится вместе с маршрутом /auth/telegram на бэкенде: там HMAC от
    // initData проверяется по токену бота, форма ответа та же, что у VK
    throw new Error('Вход из Telegram пока не поддерживается')
  },

  /** У Telegram для внешних ссылок свой метод: обычный window.open в их
   *  вебвью на части клиентов молча ничего не делает. */
  openExternal(url) {
    const app = tg()
    if (app) app.openLink(url)
    else window.open(url, '_blank', 'noopener')
  },

  /** Единственная платформа, где кнопка «назад» действительно наша. */
  onBack(handler) {
    const app = tg()
    if (!app) return () => {}

    app.BackButton.show()
    app.BackButton.onClick(handler)
    // Возвращаем отписку: без неё обработчики копятся при каждом переходе
    return () => {
      app.BackButton.offClick(handler)
      app.BackButton.hide()
    }
  },

  async setHeaderColor() {
    tg()?.setHeaderColor('#171310')
    tg()?.setBackgroundColor('#171310')
  },

  async close() {
    tg()?.close()
  },
}
