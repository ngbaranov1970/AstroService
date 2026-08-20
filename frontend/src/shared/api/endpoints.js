// Наши ручки API одной функцией на адрес. Пути и имена параметров живут
// только здесь: экраны зовут listCharts(), а не собирают строку сами.
// Вход (/auth/vk) сюда не входит — его зовёт адаптер платформы, ему http
// передают аргументом, чтобы он не зависел от этого слоя.

import { http } from './http'

/** Состояния разбора — ровно то же перечисление, что в backend/app/schemas/reports.py.
 *  Object.freeze запрещает менять объект после создания: константа, а не
 *  переменная (в Python отдельного слова для этого нет — ближе всего Enum). */
export const REPORT_STATUS = Object.freeze({
  READY: 'ready',
  RUNNING: 'running',
  LOCKED: 'locked',
  AVAILABLE: 'available',
  FAILED: 'failed',
})

/** Машинные коды отказа из ReportOut.error_code. Тексты для человека фронт
 *  подбирает сам по коду: формулировки на бэкенде меняются вместе с продуктом. */
export const ERROR_CODE = Object.freeze({
  LIMIT_EXHAUSTED: 'limit_exhausted',
  NEEDS_PURCHASE: 'needs_purchase',
  BUDGET_EXCEEDED: 'budget_exceeded',
  NOT_SUPPORTED: 'not_supported',
  FAILED: 'failed',
})

/** Подсказки городов: [{label, lat, lon}], пустой список — «не нашли».
 *  signal нужен, чтобы отменить запрос, когда человек дописал букву:
 *  ответы приходят не в том порядке, в каком уходили запросы. */
export function searchPlaces(q, { signal } = {}) {
  return http.get('/places', { params: { q }, signal })
}

/** Карты пользователя, свежие первыми: [{token, person_name, birth_local,
 *  time_known, place_name, created_at}]. */
export function listCharts() {
  return http.get('/charts')
}

/** Завести карту по данным рождения; повтор тех же данных вернёт ту же карту.
 *  data: {person_name?, birth_date: 'YYYY-MM-DD', birth_time: 'HH:MM' | null,
 *  lat, lon, place_name?}. Часовой пояс не передаём — сервер считает его
 *  по координатам сам. */
export function createChart(data) {
  return http.post('/charts', data)
}

/** Карта вместе с расчётом: поля списка плюс calc (сейчас только calc.natal). */
export function getChart(token) {
  return http.get(`/charts/${token}`)
}

/** Состояние всех разборов этой карты: [{type, title, status, sections,
 *  section_titles, price_rub, pay_url, error_code, error, created_at}].
 *  Этот же адрес опрашивается раз в несколько секунд, пока status = running. */
export function listReports(token) {
  return http.get(`/charts/${token}/reports`)
}

/** Попросить разбор. Отвечает тем же ReportOut: генерация идёт в фоне,
 *  результат догоняется опросом listReports. Повтор безопасен — готовый
 *  текст и уже идущая работа возвращают себя, не тратя лимита и денег. */
export function requestReport(token, type) {
  return http.post(`/charts/${token}/reports`, { type })
}
