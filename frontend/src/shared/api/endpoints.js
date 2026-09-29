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
 *  {joined, messages_allowed, subscribed, group_id, sun_sign}.
 *  sun_sign — знак, введённый одной датой без карты ('Дева'), или null.
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

/** Знак Солнца по одной дате рождения — короткий путь к рассылке без карты.
 *  birthDate — строка 'YYYY-MM-DD', как её собирает DateStep.
 *  Отвечает {sign: 'Дева'} или {sign: null}: в день смены знака одной даты
 *  мало, и человека ведём в полный онбординг. 409 — вход был не из VK. */
export function setSunSign(birthDate) {
  return http.post('/vk/sun-sign', { birth_date: birthDate })
}

/** Состояние заказа по его токену: {token, number, status, paid, title,
 *  price_rub, created_at, paid_at}. Спрашивается на экране возврата с оплаты —
 *  сервер при этом сам сверяется с ЮKassa и, если деньги пришли, ставит текст
 *  в очередь. */
export function getOrder(orderToken) {
  return http.get(`/orders/${orderToken}`)
}

// ─── Пары (совместимость) ──────────────────────────────────────────────────
// Ручки делятся на две группы, и граница между ними не техническая:
// /pairs требует Bearer и работает с данными двоих, /invites открыт без входа
// вовсе — по ссылке его открывает партнёр, которого у нас в базе нет.

/** Типы связи — то же перечисление, что PairKind в backend/app/models/pair.py.
 *  Подписей для человека здесь нет намеренно: их место на экране сборки пары,
 *  а сюда попадают только значения, которые уезжают на сервер. */
export const PAIR_KIND = Object.freeze({
  ROMANCE: 'romance',
  FAMILY: 'family',
})

/** Пары пользователя, свежие первыми: [{token, kind, is_complete, created_at,
 *  person_a, person_b, invite_open}].
 *  is_complete === false — пара ждёт партнёра по ссылке: расчёта и текстов
 *  ещё нет, на экране это «приглашение отправлено». */
export function listPairs() {
  return http.get('/pairs')
}

/** Собрать пару. data: {chart_a_token, chart_b_token?, kind, inviter_label?}.
 *
 *  Без chart_b_token создаётся пара, ждущая партнёра, — следующим шагом
 *  просим для неё приглашение. Повтор того же состава вернёт ту же пару.
 *  inviter_label — как назвать инициатора партнёру на анонимном экране;
 *  это отдельное поле, а не имя карты: имя карты человек вводил для себя.
 *  400 означает «так пару не собрать» (чужая карта или карта с самой собой). */
export function createPair(data) {
  return http.post('/pairs', data)
}

/** Пара вместе с расчётом синастрии: поля списка плюс calc (aspects, summary,
 *  meta с таблицей орбов). calc === null, пока партнёр не пришёл.
 *  404 приходит и на чужую пару — в отличие от карты, ссылкой на пару
 *  делиться нельзя: за ней стоят двое, и второй согласия не давал. */
export function getPair(token) {
  return http.get(`/pairs/${token}`)
}

/** Выдать ссылку-приглашение: {invite_token, expires_at}.
 *  Единственное место, где токен приглашения приходит наружу, — сам адрес
 *  ссылки собирает фронт. Живое приглашение не перевыпускается: повтор
 *  вернёт тот же токен, иначе уже отправленная ссылка перестала бы работать.
 *  409 — партнёр уже пришёл, звать некого. */
export function createInvite(token) {
  return http.post(`/pairs/${token}/invite`)
}

/** Состояние разборов этой пары — та же форма ответа, что у listReports.
 *  Поэтому экран разбора, опрос и разбор секций переиспользуются как есть. */
export function listPairReports(token) {
  return http.get(`/pairs/${token}/reports`)
}

/** Попросить разбор по паре (pair_free или pair_full). Повтор безопасен. */
export function requestPairReport(token, type) {
  return http.post(`/pairs/${token}/reports`, { type })
}

/** Завести заказ на платный разбор пары. Отвечает {token, confirmation_url}.
 *  Внутри VK отвечает 403, как и заказ по карте: продавать в Mini App нельзя. */
export function createPairOrder(token) {
  return http.post(`/pairs/${token}/orders`)
}

/** Кто зовёт и на какой разбор: {kind, inviter_label}. Вход по ссылке,
 *  авторизации не требует. inviter_label === null — инициатор не подписался,
 *  экран скажет безличное «вас приглашают».
 *  404 отвечает одинаково на «нет токена», «протух» и «уже использован»:
 *  различать их значит подтверждать существование токена тому, кто перебирает.
 *
 *  anonymous: true — заголовок с токеном этому запросу не нужен. Партнёр нам
 *  никем не представлен, и его 401 не должен запускать перевыпуск сессии. */
export function readInvite(inviteToken) {
  return http.get(`/invites/${inviteToken}`, { anonymous: true })
}

/** Партнёр вводит свои данные: пара достраивается, тизер уходит в работу.
 *  data — то же тело, что у createChart. Отвечает состоянием тизера (ReportOut),
 *  дальше его догоняем опросом readInviteReport.
 *
 *  Карта заводится в аккаунте ИНИЦИАТОРА — своего аккаунта у партнёра может
 *  не быть. Экран обязан предупредить об этом прямым текстом (152-ФЗ).
 *  400 — введённые данные совпали с данными пригласившего. */
export function acceptInvite(inviteToken, data) {
  return http.post(`/invites/${inviteToken}`, data, { anonymous: true })
}

/** Состояние тизера по принятому приглашению — тот же ReportOut.
 *  Ключ всё тот же invite_token: ссылка, которой партнёр вошёл, — его
 *  единственный вход к тексту про него самого. */
export function readInviteReport(inviteToken) {
  return http.get(`/invites/${inviteToken}/report`, { anonymous: true })
}
