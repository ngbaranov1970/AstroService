// Платформа «обычный браузер»: вход по cookie, для разработки фронта.
// Ручка /auth/web на сервере включена только вне прода (в проде отвечает
// 404): cookie заводит СВОЮ строку пользователя, не связанную с VK-строкой
// того же человека. Боевой веб-вход будет через VK ID — docs/astro-context.md,
// раздел «Идентичность пользователя».

export const webPlatform = {
  name: 'web',

  async init() {},

  /** В браузере входим по cookie: подписи VK и initData здесь нет и не будет. */
  canAuth() {
    return true
  },

  /** Обменять cookie на токен сессии. Тело пустое — личность едет в cookie,
   *  браузер прикладывает её к запросу на свой же origin сам. */
  async obtainToken(http) {
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

  async setHeaderColor() {},

  async close() {},
}
