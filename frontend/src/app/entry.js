// Вход по ссылке из меню сообщества: с какой плитки пришёл человек и куда его
// за этим вести. Зовут роутер (куда вести), онбординг (проверка ?next) и
// адаптер VK (метка входа для аналитики). Сам адрес не меняет — только читает.
// Почему так устроено — docs/frontend-plan.md, «Входы по ссылке».

// Цели плиток — белый список. Всё, что не из него, считается «цели нет»:
// ?next= может собрать кто угодно, и увести по нему на чужой адрес нельзя
export const GOALS = Object.freeze(['natal', 'pair', 'horoscope'])

/** Привести кусок адреса к цели из GOALS; null — такой цели нет.
 *  Терпимо к форме: клиенты VK отдают хеш по-разному — '#natal',
 *  '#/natal', '#%2Fnatal'. Годится и для хеша, и для ?next из онбординга. */
export function parseGoal(raw) {
  // typeof — проверка типа значения, как isinstance(raw, str) в Python.
  // ?next= может прийти и массивом (?next=a&next=b), его не разбираем
  if (typeof raw !== 'string') return null

  let text
  // decodeURIComponent превращает '%2F' обратно в '/', как urllib.parse.unquote.
  // На битой последовательности ('%E0') он бросает ошибку — это не наша цель
  try {
    text = decodeURIComponent(raw)
  } catch {
    return null
  }

  // Регулярное выражение между косыми чертами: ^[#/]+ — все # и / в начале
  // строки, \/+$ — все / в конце; флаг g — заменить оба места, а не первое.
  // В Python то же самое — re.sub(r'^[#/]+|/+$', '', text)
  text = text.replace(/^[#/]+|\/+$/g, '').toLowerCase()

  // includes — проверка «есть ли в списке», как `text in GOALS`
  return GOALS.includes(text) ? text : null
}

// Цель этого запуска. Читаем один раз при загрузке модуля: роутер следом
// перепишет '#natal' в '#/start/natal', и исходный хеш будет уже не достать.
// Модуль выполняется один раз, так что значение общее на всё приложение
export const launchGoal = parseGoal(window.location.hash)

/** Куда вести человека с этой целью: объект маршрута для router.push/replace.
 *  chartToken — карта, от которой считать разбор; без неё цель «карта»
 *  вести некуда, и человек попадает на главную. Неизвестная цель — тоже. */
export function goalRoute(goal, chartToken) {
  // Бесплатный разбор своей карты. query.start просит экран разбора сразу
  // заказать текст: иначе человек после онбординга упрётся ещё в одну кнопку
  if (goal === 'natal' && chartToken) {
    return {
      name: 'report',
      params: { token: chartToken, type: 'free_natal' },
      query: { start: '1' },
    }
  }

  // Сборка пары: своя карта уже есть, дальше человек вводит партнёра
  if (goal === 'pair') return { name: 'pair-create' }

  // Экран подписки на гороскоп в личные сообщения
  if (goal === 'horoscope') return { name: 'horoscope' }

  return { name: 'home' }
}
