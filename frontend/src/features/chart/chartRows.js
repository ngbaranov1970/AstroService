// Перевод расчёта calc.natal в строки таблиц экрана карты. Зовёт его только
// ChartView.vue; своих запросов и состояния здесь нет — чистые функции.
//
// Формат позиции — градусы с минутами (11°08′), а не целые градусы, как на
// стенде: таблицу открывают ради сверки, а «0° Рак» у Солнца на границе знака
// сверить нельзя. Минуты округляем, как scripts/natal_cli.py: усечение
// расходилось бы с нашим же сверщиком на минуту.

// Строка таблицы: слева имя, посередине значение, справа приписка.
// Все три функции ниже возвращают такие же объекты, поэтому таблица одна на всех.

/** Градусы внутри знака (11.1314) → «11°08′». Пустое значение → прочерк. */
export function formatDegree(deg) {
  // typeof — встроенная проверка типа значения; null и undefined числами не
  // являются, а из чужого ответа сюда может приехать что угодно
  if (typeof deg !== 'number') return '—'

  // Math.trunc отбрасывает дробную часть (аналог int() в Python)
  let whole = Math.trunc(deg)
  let minutes = Math.round((deg - whole) * 60)

  // 59,5 минуты и больше округлились в целый градус — переносим, иначе на
  // экране появится «11°60′». Тот же перенос делает scripts/natal_cli.py
  if (minutes === 60) {
    whole += 1
    minutes = 0
  }

  // padStart дополняет строку слева до нужной длины: 8 → «08», иначе колонка
  // выглядит рваной. String(...) нужен потому, что padStart есть у строк, не у чисел
  return `${whole}°${String(minutes).padStart(2, '0')}′`
}

/** Планеты: имя, позиция в знаке, приписка «R» и номер дома.
 *  timeKnown=false — дома посчитаны от полудня и означают чужую карту,
 *  поэтому их не показываем (то же правило, что у стенда и бейджей главной). */
export function planetRows(natal, timeKnown) {
  // ?? [] — если planets нет (расчёт не догрузился), перебираем пустой список,
  // а не падаем на undefined.map
  return (natal?.planets ?? []).map((planet) => ({
    key: planet.name,
    name: planet.name_ru,
    value: `${formatDegree(planet.deg_in_sign)} ${planet.sign_ru}`,
    // filter(Boolean) выбрасывает пустые куски, join склеивает остаток через
    // пробел: получается «R 3 дом», «R», «3 дом» или пустая строка
    note: [planet.retrograde ? 'R' : '', timeKnown && planet.house ? `${planet.house} дом` : '']
      .filter(Boolean)
      .join(' '),
  }))
}

/** Углы и дома одной таблицей: сначала Асцендент и MC, за ними 12 куспидов.
 *  Зовётся только при известном времени — без него всё это посчитано от полудня. */
export function houseRows(natal) {
  const angles = natal?.angles ?? {}

  // Асцендент и MC лежат в объекте по именам, а не списком — собираем вручную.
  // Boolean(...) в filter отсеет отсутствующий угол
  const angleRows = [angles.asc, angles.mc].filter(Boolean).map((angle) => ({
    key: angle.name,
    name: angle.name_ru,
    value: `${formatDegree(angle.deg_in_sign)} ${angle.sign_ru}`,
    note: '',
  }))

  const cuspRows = (natal?.houses ?? []).map((house) => ({
    key: `house-${house.num}`,
    name: `${house.num} дом`,
    value: `${formatDegree(house.deg_in_sign)} ${house.sign_ru}`,
    note: '',
    // Куспиды — служебные числа рядом с двумя главными углами: приглушаем имя
    dim: true,
  }))

  // ... — спред: раскладывает элементы обоих списков в один новый
  return [...angleRows, ...cuspRows]
}

/** Аспекты: пара точек, тип и орб. Порядок берём как есть — ядро уже
 *  отдаёт их по планетам, а своя сортировка разъехалась бы с natal.py. */
export function aspectRows(natal) {
  return (natal?.aspects ?? []).map((aspect) => ({
    key: `${aspect.p1}-${aspect.p2}-${aspect.type}`,
    name: `${aspect.p1_ru} — ${aspect.p2_ru}`,
    value: aspect.type_ru,
    note: `орб ${aspect.orb}°`,
  }))
}
