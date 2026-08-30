// Плитки главной: реестр видов разбора плюс ответ API = готовый список того,
// что рисовать. Зовёт этот модуль только HomeView, данные передаёт он же.
// Функция чистая — ни реактивных переменных, ни запросов, только вход и выход:
// её можно позвать с выдуманным списком и проверить результат.

import { describe, REPORT_TYPES } from '@/features/report/reportTypes'
import { REPORT_STATUS } from '@/shared/api/endpoints'

// Подпись под заголовком плитки на каждое состояние разбора.
// Ключ в квадратных скобках — значение переменной, а не слово «REPORT_STATUS»:
// так подписи привязаны к тем же константам, что и проверки в коде
const SUBTITLE = Object.freeze({
  [REPORT_STATUS.READY]: 'Разбор готов',
  [REPORT_STATUS.RUNNING]: 'Готовится',
  [REPORT_STATUS.AVAILABLE]: 'Можно получить',
  [REPORT_STATUS.LOCKED]: 'Откроется после покупки',
  [REPORT_STATUS.FAILED]: 'Не получилось, откройте и повторите',
})

// Плитки, за которыми нет разбора: сервер про них ничего не знает, реестр
// видов — тоже, а в макете они есть. Форма записи та же, что у собранных из
// API, чтобы компонент плитки не различал их происхождение
const EXTRA_TILES = Object.freeze([
  {
    key: 'pair',
    order: 2, // между натальной картой и узкой парой «Матрица + Восточный»
    icon: 'pair',
    shape: 'wide',
    tone: 'dim',
    soon: true,
    title: 'Совместимость',
    subtitle: 'Сравни карты с партнёром',
    to: { name: 'soon', params: { feature: 'pair' } },
  },
])

// Текст пунктирной карточки-анонса под плитками (DashedCard в макете B_2)
export const HOME_NOTE = 'Скоро: прогноз на год и карты близких'

/** Подпись-статус плитки. У работающего разбора это его состояние, у
 *  анонса — из чего он считается.
 *
 *  Про полный разбор здесь ничего нет намеренно: он предлагается внутри
 *  бесплатного, и хвост «полный из 6 секций» на главной обещал раньше, чем
 *  человек прочитал первый текст. */
function subtitleFor(meta, report) {
  // Типа нет в ответе сервера — генерации у него не бывает вовсе (matrix,
  // eastern: промпта нет)
  if (!report || meta.comingSoon) return meta.tileNote ?? 'Скоро'

  // Незнакомый статус тоже не должен оставлять пустоту
  return SUBTITLE[report.status] ?? 'Открыть'
}

/** Одна плитка из описания типа и (если он есть) состояния разбора с сервера. */
function toTile(token, meta, byType) {
  const report = byType.get(meta.type) ?? null
  // Разбор работает: сервер про него знает и расчёт для него есть
  const isLive = Boolean(report) && !meta.comingSoon

  return {
    key: meta.type,
    order: meta.order,
    icon: meta.icon,
    shape: meta.shape,
    // Главная делит плитки на две секции по этому признаку, а не по цвету
    // рамки: tone — про внешний вид, и завтра он может стать другим
    soon: !isLive,
    // Название берём с сервера, когда оно там есть: ReportOut.title — то же
    // имя, которым товар назван на странице оплаты, расходиться им нельзя.
    // ?. — обращение к полю, безопасное к null: у пустой ссылки вернётся
    // undefined вместо ошибки (в Python приходится писать x and x.title)
    title: report?.title ?? meta.tileTitle ?? 'Разбор',
    subtitle: subtitleFor(meta, report),
    // Яркость рамки: золотая только у того, что реально открывается
    tone: isLive ? 'active' : 'dim',
    to: isLive
      ? { name: 'report', params: { token, type: meta.type } }
      : { name: 'soon', params: { feature: meta.type } },
  }
}

/** Плитки главной в порядке показа. token нужен для адреса разбора, reports —
 *  items из useReports (пустой список, пока ответа нет: плитки рисуются и
 *  тогда, просто все со статусом «скоро»). */
export function buildHomeTiles(token, reports) {
  // Map — словарь, ключом которого может быть что угодно; конструктор берёт
  // массив пар [ключ, значение], как dict() от списка кортежей в Python
  const byType = new Map(reports.map((item) => [item.type, item]))

  // Set — множество без повторов, спред разворачивает его обратно в массив.
  // Объединяем известное фронту и присланное сервером: знакомый тип рисуется
  // до первого ответа API, а незнакомый (новый продукт бэкенда) — сразу
  const types = [...new Set([...Object.keys(REPORT_TYPES), ...byType.keys()])]

  const tiles = types
    .map((type) => describe(type))
    .filter((meta) => meta.onHome)
    .map((meta) => toTile(token, meta, byType))

  // sort меняет массив на месте, поэтому сортируем новый (спред делает копию).
  // Компаратор возвращает отрицательное число, когда первый идёт раньше
  return [...tiles, ...EXTRA_TILES].sort((a, b) => a.order - b.order)
}
