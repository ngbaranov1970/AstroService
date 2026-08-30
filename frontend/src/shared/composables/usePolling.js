// Опрос сервера, пока чего-то ждём: генерация разбора идёт 30-60 секунд, и
// узнать о её конце можно только повторным запросом. Зовёт этот файл экран
// разбора, сам он зовёт переданную функцию и про API ничего не знает.
//
// Три вещи, ради которых это отдельный файл, а не пять строк в компоненте:
// таймер обязан умирать вместе с экраном, свёрнутое приложение не должно
// стучаться в сеть, а бесконечное ожидание нужно чем-то обрывать.

import { onUnmounted, ref, watch } from 'vue'

const INTERVAL_MS = 3000
// Дольше трёх минут генерация не живёт: LLM отвечает за минуту, а зависший
// запрос всё равно кончится ошибкой на сервере
const TIMEOUT_MS = 3 * 60 * 1000

/** Звать tick раз в несколько секунд, пока active истинно.
 *
 *  active — реактивное значение (ref или computed) с булевым содержимым:
 *  опрос идёт сам, включать и выключать его руками не нужно.
 *  Возвращает { timedOut } — признак «ждём слишком долго, опрос прекращён».
 *  Ошибки tick не ловит: это дело вызывающего, у него и состояние.
 */
export function usePolling(tick, active, { intervalMs = INTERVAL_MS, timeoutMs = TIMEOUT_MS } = {}) {
  const timedOut = ref(false)

  // Обычные переменные, не ref: шаблону они не нужны, а лишняя реактивность
  // заставляла бы Vue следить за тем, на что никто не смотрит
  let timer = null
  let startedAt = 0
  let busy = false // предыдущий запрос ещё в пути

  function stop() {
    if (timer === null) return

    // clearInterval гасит таймер по его номеру; setInterval возвращает этот
    // номер при запуске. Забыть про него — значит оставить таймер работать
    // после ухода с экрана
    clearInterval(timer)
    timer = null
  }

  async function run() {
    // Сеть тормозит сильнее, чем идут тики: без этой проверки запросы
    // накладывались бы друг на друга
    if (busy) return

    // Отсчёт ведём от старта, а не считаем тики: свёрнутое приложение таймер
    // останавливает, и «шестьдесят тиков» растянулись бы на весь день
    if (Date.now() - startedAt > timeoutMs) {
      timedOut.value = true
      stop()
      return
    }

    busy = true
    try {
      // await ждёт результат асинхронной функции. Функция с async всегда
      // возвращает промис — обещание значения, которое придёт позже; await
      // разворачивает его обратно в значение
      await tick()
    } finally {
      // finally выполняется и после ошибки: иначе одна неудача заперла бы
      // опрос флагом busy навсегда
      busy = false
    }
  }

  function start() {
    if (timer !== null) return // уже идёт

    timedOut.value = false
    startedAt = Date.now()
    // setInterval зовёт функцию раз в N миллисекунд, пока его не погасят
    timer = setInterval(run, intervalMs)
  }

  /** Приложение свернули или развернули. */
  function onVisibility() {
    if (!active.value) return

    // document.visibilityState — 'hidden', когда вкладка не на экране. В VK
    // так выглядит свёрнутое приложение: опрашивать сервер оттуда незачем
    if (document.visibilityState === 'hidden') {
      stop()
      return
    }

    // Вернулись — спрашиваем сразу, не дожидаясь тика: за время отсутствия
    // разбор мог давно дописаться, а человек смотрит на старый экран
    start()
    run()
  }

  document.addEventListener('visibilitychange', onVisibility)

  // watch следит за значением; immediate — позвать сразу, а не только при
  // следующей смене: разбор мог уже быть в работе, когда экран открыли
  watch(
    active,
    (value) => {
      if (value) start()
      else stop()
    },
    { immediate: true },
  )

  // onUnmounted — «экран убрали со страницы». Без уборки таймер и подписка
  // остались бы висеть: переход на главную и обратно копил бы их пачками
  onUnmounted(() => {
    stop()
    document.removeEventListener('visibilitychange', onVisibility)
  })

  return { timedOut }
}
