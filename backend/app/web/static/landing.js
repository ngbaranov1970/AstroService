// Колесо натальной карты в первом экране лендинга (templates/landing.html).
// Рисуем скриптом, а не разметкой: координата каждой точки считается из её
// долготы, и руками такое не поддержать — при смене эталона поехало бы всё.

// const — переменная, которую нельзя переприсвоить (примерно как Final в
// аннотациях Python). Дальше в файле все имена такие: ничего не меняется.
const CX = 200;
const CY = 200;

// Радиусы колец в системе координат SVG (viewBox 400×400 из шаблона)
const OUTER = 192;   // еле заметный внешний край
const SIGNS_RING = 158;  // граница кольца знаков, от неё же идут оси
const INNER = 112;   // внутреннее кольцо, ниже него ничего не рисуем
const RING = 132;    // радиус, на котором стоят символы планет
const STEP = 21;     // насколько опускается планета, попавшая в тесноту

// Долгота эклиптики: номер знака × 30 + градус внутри знака. Числа — из карты
// 1990-05-11 17:20 Касимов (backend/tests/golden). ТЕ ЖЕ значения напечатаны
// таблицей в landing.html: меняете эталон — меняйте оба места.
const ASC = 193.78;  // 13°47' Весы, от него разворачивается всё колесо
const MC = 108.90;   // 18°54' Рак

// Массив объектов: у каждой планеты символ и долгота. Фигурные скобки внутри
// квадратных — это список словарей, как [{...}, {...}] в Python.
// Символ записан с невидимым знаком U+FE0E: без него браузер рисует цветное
// эмодзи вместо золотого глифа.
const PLANETS = [
  { glyph: '☉︎', lon: 50.58 },
  { glyph: '☽︎', lon: 249.60 },
  { glyph: '☿︎', lon: 39.05 },
  { glyph: '♀︎', lon: 8.33 },
  { glyph: '♂︎', lon: 345.40 },
  { glyph: '♃︎', lon: 98.80 },
  { glyph: '♄︎', lon: 295.30 },
  { glyph: '♅︎', lon: 279.28 },
  { glyph: '♆︎', lon: 284.40 },
  { glyph: '♇︎', lon: 226.28 },
];

const SIGNS = ['♈︎', '♉︎', '♊︎', '♋︎', '♌︎', '♍︎', '♎︎', '♏︎', '♐︎', '♑︎', '♒︎', '♓︎'];

/**
 * Точка на окружности по долготе и радиусу.
 *
 * В астрологическом колесе асцендент всегда слева, а зодиак разворачивается от
 * него против часовой стрелки — отсюда «180 + (долгота − асцендент)».
 * Возвращает пару [x, y].
 */
function point(lon, radius) {
  // Math.cos и Math.sin принимают радианы, а долгота у нас в градусах
  const angle = (180 + (lon - ASC)) * Math.PI / 180;
  // Ось Y в SVG направлена вниз, поэтому синус вычитается, а не прибавляется
  return [CX + radius * Math.cos(angle), CY - radius * Math.sin(angle)];
}

/** Линия между двумя долготами на одном радиусе — оси карты рисуются ею. */
function axis(lon, radius, opacity) {
  const [x1, y1] = point(lon, radius);
  const [x2, y2] = point(lon + 180, radius);
  // Обратные кавычки — шаблонная строка, аналог f-строки Python: то, что
  // стоит в ${...}, подставляется значением. toFixed(1) округляет до одного
  // знака после запятой, иначе в разметку уедут числа на двадцать цифр.
  return `<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}"
                stroke="currentColor" stroke-width="1" opacity="${opacity}"/>`;
}

// Куски SVG копим в массиве и склеиваем одной строкой в конце: каждая
// отдельная вставка в документ заставляла бы браузер пересчитывать страницу
const parts = [];

// Три кольца. currentColor — «цвет текста этого элемента»: он задан классом
// .wheel в CSS, и колесо меняет цвет вместе с темой, а не мимо неё
parts.push(`<circle cx="${CX}" cy="${CY}" r="${OUTER}" fill="none" stroke="currentColor" stroke-width="1" opacity="0.18"/>`);
parts.push(`<circle cx="${CX}" cy="${CY}" r="${SIGNS_RING}" fill="none" stroke="currentColor" stroke-width="1" opacity="0.45"/>`);
parts.push(`<circle cx="${CX}" cy="${CY}" r="${INNER}" fill="none" stroke="currentColor" stroke-width="1" opacity="0.18"/>`);

// Кольцо знаков: двенадцать секторов по 30°, разделитель на границе и символ
// посередине. forEach — обход списка, index здесь номер знака от нуля.
const zodiac = [];
SIGNS.forEach((glyph, index) => {
  const start = index * 30;

  const [x1, y1] = point(start, SIGNS_RING);
  const [x2, y2] = point(start, OUTER);
  zodiac.push(`<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}"
                     stroke="currentColor" stroke-width="1" opacity="0.3"/>`);

  // Символ ставим в середину сектора — на 15° от его начала
  const [tx, ty] = point(start + 15, 175);
  zodiac.push(`<text x="${tx.toFixed(1)}" y="${ty.toFixed(1)}" fill="currentColor" font-size="15"
                     text-anchor="middle" dominant-baseline="central" opacity="0.85">${glyph}</text>`);
});

// Знаки кладём отдельной группой: класс .wheel-zodiac вращает её целиком
parts.push(`<g class="wheel-zodiac">${zodiac.join('')}</g>`);

// Горизонт (асцендент — десцендент) ярче меридиана: именно от него человек
// читает карту, MC служит ориентиром
parts.push(axis(ASC, SIGNS_RING, 0.5));
parts.push(axis(MC, SIGNS_RING, 0.28));

// Планеты, стоящие рядом, налезают символами друг на друга: в этой карте Уран,
// Нептун и Сатурн умещаются в 16° Козерога. Опускаем на радиус ниже каждую,
// что ближе 11° к предыдущей. Градус при этом не врёт — его показывает штрих
// у кольца, а символ только подписывает.
//
// [...PLANETS] — спред: копия массива. Сортируем копию, потому что sort()
// переставляет элементы на месте, а исходный список пусть остаётся как есть.
const placed = [...PLANETS].sort((first, second) => first.lon - second.lon);

let previousLon = -999;
let level = 0;
placed.forEach((planet) => {
  level = planet.lon - previousLon < 11 ? level + 1 : 0;
  // Тернарный оператор выше — это «A if условие else B» из Python, только
  // порядок другой: условие ? тогда : иначе
  planet.radius = RING - level * STEP;
  previousLon = planet.lon;
});

// Штрих от кольца к символу и сам символ. Деструктуризация в скобках —
// разбор объекта на переменные, как `glyph, lon = ...` по ключам словаря.
placed.forEach(({ glyph, lon, radius }) => {
  const [sx, sy] = point(lon, SIGNS_RING);
  const [ex, ey] = point(lon, radius + 11);
  parts.push(`<line x1="${sx.toFixed(1)}" y1="${sy.toFixed(1)}" x2="${ex.toFixed(1)}" y2="${ey.toFixed(1)}"
                    stroke="currentColor" stroke-width="1" opacity="0.5"/>`);

  const [px, py] = point(lon, radius);
  parts.push(`<text x="${px.toFixed(1)}" y="${py.toFixed(1)}" fill="currentColor" font-size="17"
                    text-anchor="middle" dominant-baseline="central">${glyph}</text>`);
});

// В центре только точка пересечения осей. Полумесяц эмблемы стоял бы рядом с
// символом Луны и читался бы как ещё одно светило.
parts.push(`<circle cx="${CX}" cy="${CY}" r="2.5" fill="currentColor" opacity="0.7"/>`);

// innerHTML — содержимое элемента разметкой. Скрипт подключён в конце body,
// поэтому <svg id="wheel"> к этому моменту уже разобран браузером и найдётся.
document.getElementById('wheel').innerHTML = parts.join('');
