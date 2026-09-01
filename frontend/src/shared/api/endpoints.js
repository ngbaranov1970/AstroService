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

/** Завести заказ на платный разбор этой карты. Отвечает
 *  {token, confirmation_url} — по второму адресу лежит форма оплаты ЮKassa,
 *  на неё уходим переходом в этой же вкладке.
 *
 *  Внутри VK эта ручка отвечает 403: продавать в Mini App запрещено, поэтому
 *  звать её оттуда нельзя (плитка «Полный разбор» и не зовёт).
 *  Повторный вызов не заводит второго платежа — вернётся тот же заказ.
 *  409 означает «уже оплачено». */
export function createOrder(token) {
  return http.post(`/charts/${token}/orders`)
}

/** Оценка разбора: 1–5 звёзд и необязательное «что не так».
 *  Ответ пустой (204 No Content) — экран и так знает, что человек нажал.
 *  Владельцем карты быть не обязательно: мнение того, кому переслали
 *  ссылку, нам нужно не меньше. */
export function leaveFeedback(token, type, rating, comment = '') {
  return http.post(`/charts/${token}/feedback`, { type, rating, comment })
}

/** Состояние человека в нашем сообществе VK:
 *  {joined, messages_allowed, group_id}.
 *  joined === null означает «выяснить не удалось» (VK не ответил или
 *  сообщество не настроено) — в этом случае кнопку подписки всё равно
 *  показываем: предложить лишний раз дешевле, чем спрятать её у того, кто
 *  на самом деле не подписан.
 *  Вне VK отвечает {joined: null, messages_allowed: false, group_id: null}. */
export function getGroupState() {
  return http.get('/vk/group-state')
}

/** Запомнить у себя, что человек разрешил сообществу писать.
 *  Зовётся ПОСЛЕ того, как VK Bridge ответил согласием: список разрешивших
 *  VK не отдаёт, и наша запись — единственный источник правды для рассылки.
 *  Ответ пустой. 409 — вход был не из VK. */
export function allowGroupMessages() {
  return http.post('/vk/messages-allowed')
}

/** Погасить разрешение писать. В VK при этом ничего не меняется — отменить
 *  выданное разрешение Bridge не умеет вовсе, — но рассылка берёт
 *  получателей по нашему флагу, и писать мы перестаём сразу. */
export function denyGroupMessages() {
  return http.post('/vk/messages-denied')
}

/** Состояние заказа по его токену: {token, number, status, paid, title,
 *  price_rub, created_at, paid_at}. Спрашивается на экране возврата с оплаты —
 *  сервер при этом сам сверяется с ЮKassa и, если деньги пришли, ставит текст
 *  в очередь. */
export function getOrder(orderToken) {
  return http.get(`/orders/${orderToken}`)
}
