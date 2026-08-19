// Заглушка платформы «обычный браузер»: контракт есть, авторизации нет.
// Веб по стратегии проекта — платёжный и SEO-хаб на Jinja, а не это
// приложение; сюда фронт поедет позже, вход будет по cookie-сессии.

export const webPlatform = {
  name: 'web',

  async init() {},

  /** Авторизоваться нечем: у браузера нет ни подписи VK, ни initData. */
  canAuth() {
    return false
  },

  async obtainToken() {
    // Появится, когда в API добавится вход по cookie: там же, где сейчас
    // /auth/vk, будет /auth/web, а тело запроса окажется пустым
    throw new Error('Вход в вебе пока не поддерживается')
  },

  /** В браузере внешняя ссылка — обычная новая вкладка. */
  openExternal(url) {
    window.open(url, '_blank', 'noopener')
  },

  onBack() {
    return () => {}
  },

  async setHeaderColor() {},

  async close() {},
}
