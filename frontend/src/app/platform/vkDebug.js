// ВРЕМЕННО: диагностика входа с плиток на Android — что присылает клиент VK,
// когда разворачивает свёрнутое приложение по новой ссылке. Включается ссылкой
// с #debug, выключается #nodebug. Зовёт vk.js в init(); удалить после выяснения.

import bridge from '@vkontakte/vk-bridge'

const FLAG = 'astro-debug'
// Сколько последних строк держим на плашке
const MAX_LINES = 14

/** Включена ли диагностика: флаг ставит/снимает хеш запуска, живёт в localStorage.
 *  localStorage — хранилище браузера «ключ → строка», переживает перезапуск. */
function isEnabled() {
  try {
    // Клиенты VK отдают хеш по-разному ('#debug', '#/debug', '#%2Fdebug') —
    // снимаем кодировку и все # и / в начале, как parseGoal в entry.js
    const word = decodeURIComponent(window.location.hash).replace(/^[#/]+/, '')
    if (word === 'debug') localStorage.setItem(FLAG, '1')
    if (word === 'nodebug') localStorage.removeItem(FLAG)
    return localStorage.getItem(FLAG) === '1'
  } catch {
    // Хранилище бывает недоступно — тогда просто без диагностики
    return false
  }
}

/** Завести плашку и вернуть функцию «дописать строку». */
function createPanel() {
  const panel = document.createElement('pre')
  // cssText — все стили строкой разом. pointer-events: none — нажатия
  // проходят сквозь плашку к экрану под ней
  panel.style.cssText =
    'position:fixed;left:0;right:0;bottom:60px;z-index:99999;margin:0;' +
    'padding:6px;font:11px/1.3 monospace;color:#0f0;background:rgba(0,0,0,.85);' +
    'white-space:pre-wrap;pointer-events:none;'
  document.body.appendChild(panel)

  const lines = []
  return (text) => {
    // Время с точностью до секунды: видно, какие строки пришли одной пачкой
    const time = new Date().toTimeString().slice(0, 8)
    lines.push(`${time} ${text}`)
    // splice вырезает лишние строки с начала — остаются последние MAX_LINES
    if (lines.length > MAX_LINES) lines.splice(0, lines.length - MAX_LINES)
    panel.textContent = lines.join('\n')
  }
}

/** Безопасная выжимка параметров запуска: без подписи и id. */
function summary(params) {
  return `vk_ts=${params?.vk_ts} vk_ref=${params?.vk_ref}`
}

export function startVkDebug(launchParams) {
  if (!isEnabled()) return
  const log = createPanel()

  log(`start hash=${window.location.hash} ${summary(launchParams)}`)

  // Все события моста: тип и, если есть, location — ради него всё и затеяно
  bridge.subscribe((event) => {
    const { type, data } = event.detail
    // Object.keys — список ключей объекта, как list(d) в Python
    const keys = data ? Object.keys(data).join(',') : ''
    log(`${type} location=${data?.location} keys=${keys}`)

    // Развернули — спрашиваем параметры запуска заново: сменился vk_ts —
    // значит, клиент знает о новом входе и новую плитку можно достать
    if (type === 'VKWebAppViewRestore') {
      bridge
        .send('VKWebAppGetLaunchParams')
        .then((params) => log(`relaunch? ${summary(params)}`))
        .catch((error) => log(`GetLaunchParams error ${error?.error_data?.error_code}`))
    }
  })

  // События самой страницы — на случай, если клиент меняет адрес, а не шлёт событие
  window.addEventListener('hashchange', () => log(`hashchange ${window.location.hash}`))
  document.addEventListener('visibilitychange', () =>
    log(`visibility ${document.visibilityState} hash=${window.location.hash}`),
  )
}
