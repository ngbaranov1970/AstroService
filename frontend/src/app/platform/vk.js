// Адаптер платформы VK: единственное место во фронте, где зовётся vk-bridge.
// Остальной код работает с контрактом (init/obtainToken/openExternal/...),
// поэтому тот же фронт поедет в Telegram и веб — там будут свои адаптеры.

import bridge from '@vkontakte/vk-bridge'

// Параметры запуска, снятые при старте: sign и все vk_*. Читаются один раз —
// внутри приложения адрес меняет роутер, и вернуться к исходному нельзя
let launchParams = null

// Наш фон из токенов дизайна. Дублируем строкой: bridge принимает цвет
// значением, CSS-переменную он прочитать не может
const BG = '#171310'

/** Снять параметры запуска из адреса; null, если открыто не из VK.
 *  VK кладёт их в query-строку iframe, а роутер у нас hash-овый и
 *  location.search не трогает — значит параметры доживают до авторизации. */
function readLaunchParams() {
  const search = new URLSearchParams(window.location.search)
  // Object.fromEntries собирает объект из пар [ключ, значение] — обратная
  // операция к Object.entries, примерно как dict(pairs) в Python
  const params = Object.fromEntries(search.entries())
  // Подпись — признак того, что это запуск мини-приложения. Нет её —
  // страницу открыли напрямую в браузере, и авторизовать некого
  return params.sign ? params : null
}

export const vkPlatform = {
  name: 'vk',

  /** Инициализация клиента VK. Зовётся из main.js ДО монтирования Vue. */
  async init() {
    launchParams = readLaunchParams()
    // VKWebAppInit — сигнал «приложение загрузилось, убирай прелоадер». Без
    // него страница видна, но накрыта крутилкой VK, и тапы уходят сквозь неё
    await bridge.send('VKWebAppInit', {})
  },

  /** Есть ли чем авторизоваться на этой платформе. */
  canAuth() {
    return launchParams !== null
  },

  /** Обменять параметры запуска на сессию: {token, expires_in, user}.
   *  http передаётся аргументом, чтобы адаптер не зависел от api-слоя.
   *  Бросает ApiError: 401 — подпись не принята, 503 — вход из VK не настроен
   *  в окружении (пустой VK_SECURE_KEY на бэкенде). */
  async obtainToken(http) {
    if (!launchParams) {
      throw new Error('Приложение открыто не из VK — параметров запуска нет')
    }
    // anonymous: true — этому запросу не нужен заголовок с токеном, и его 401
    // не должен запускать перевыпуск: перевыпуск и есть этот запрос
    return await http.post('/auth/vk', { params: launchParams }, { anonymous: true })
  },

  /** Открыть внешнюю ссылку (оплата) в браузере поверх приложения.
   *  Оплата обязана уходить наружу: цифровые товары внутри Mini App
   *  разрешены только за «голоса», а на iOS запрещены вовсе. */
  openExternal(url) {
    // noopener — открытая вкладка не получит ссылку на наше окно (window.opener)
    window.open(url, '_blank', 'noopener')
  },

  /** Подписка на кнопку «назад». Возвращает функцию отписки.
   *  У VK своей кнопки нет: на Android аппаратную обрабатывает сам клиент,
   *  на iOS её нет вовсе. Метод существует ради общего контракта — в
   *  Telegram он подпишется на BackButton, и экраны переписывать не придётся. */
  onBack() {
    return () => {}
  },

  /** Покрасить шапку и статус-бар под наш фон.
   *  Без этого над приложением висит белая полоса — на тёмном макете заметно. */
  async setHeaderColor() {
    try {
      await bridge.send('VKWebAppSetViewSettings', {
        // light — светлые значки на тёмном фоне (часы, батарея)
        status_bar_style: 'light',
        action_bar_color: BG,
      })
    } catch {
      // На desktop_web метода нет, и это не ошибка: шапку там рисует сама VK.
      // catch без переменной — «поймать и не смотреть что», нужен для тишины
    }
  },

  /** Закрыть мини-приложение (вернуться в VK). */
  async close() {
    try {
      await bridge.send('VKWebAppClose', { status: 'success' })
    } catch {
      // В desktop_web и в обычном браузере закрывать нечего
    }
  },
}
