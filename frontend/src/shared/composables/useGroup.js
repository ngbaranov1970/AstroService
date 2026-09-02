// Состояние человека в нашем сообществе VK: подписан ли, разрешил ли писать.
// Единственное место во фронте, где эти три ручки API и два окна VK Bridge
// связаны в один сценарий. Состояние объявлено на уровне модуля — модуль
// выполняется один раз, поэтому оно общее на всё приложение: спросили один
// раз, а показывают его и профиль, и конец разбора.
//
// Зачем это вообще: разрешение писать — единственный канал повторного
// контакта. Список разрешивших VK не отдаёт, и наша запись о нём —
// единственный источник правды для рассылки (docs/astro-context.md,
// «Контур сообщества VK»).

import { computed, ref } from 'vue'

import { platform } from '@/app/platform'
import { allowGroupMessages, denyGroupMessages, getGroupState } from '@/shared/api/endpoints'

const status = ref('idle') // idle | loading | ready | error
// null — выяснить не удалось: VK не ответил или сообщество не настроено.
// Считаем такого человека неподписанным: предложить лишний раз дешевле, чем
// спрятать кнопку у того, кто на самом деле не подписан
const joined = ref(null)
const messagesAllowed = ref(false)
const groupId = ref(null)

// Промис идущего запроса: два экрана, открытые подряд, ждут один ответ,
// а не заводят два (тот же приём single-flight, что в useSession)
let pending = null

/** Спросить состояние у сервера. Не бросает: ошибка видна по status. */
function load() {
  status.value = 'loading'

  pending = getGroupState()
    // .then(...) — «когда промис выполнится, сделай это». Здесь то же, что
    // await, но без async-функции вокруг
    .then((state) => {
      joined.value = state.joined
      messagesAllowed.value = state.messages_allowed
      groupId.value = state.group_id
      status.value = 'ready'
    })
    .catch(() => {
      // Текста ошибки нет намеренно: блок подписки не главное на экране,
      // и при отказе он просто не рисуется
      status.value = 'error'
    })
    .finally(() => {
      pending = null
    })

  return pending
}

/** Загрузить состояние один раз за запуск приложения.
 *  Зовут монтирующиеся компоненты; от повторов защищает сам. */
function ensureLoaded() {
  if (status.value === 'ready' || status.value === 'error') return
  if (pending) return pending
  return load()
}

/** Открыть окно VK «Подписаться».
 *  Бросает Error с готовой для человека фразой: и отказ, и молчание клиента
 *  VK для нас одно и то же — подписки не случилось. */
async function join() {
  try {
    await platform.joinGroup(groupId.value)
  } catch {
    throw new Error('Не получилось подписаться. Попробуй ещё раз.')
  }

  // Своего запроса к нам тут нет: подписка живёт в VK, и при следующем
  // заходе её подтвердит groups.isMember на бэкенде
  joined.value = true
}

/** Окно VK «Разрешить сообщению писать», затем запись разрешения у себя.
 *  Бросает Error с фразой для человека, если VK разрешения не дал. */
async function allow() {
  let granted = false

  try {
    granted = await platform.allowMessages(groupId.value)
  } catch {
    throw new Error('Разрешение не выдано. Без него мы не сможем написать.')
  }

  if (!granted) {
    throw new Error('VK не подтвердил разрешение. Попробуй ещё раз.')
  }

  // Флаг поднимаем сразу после ответа VK, до запроса к нам: для человека
  // решает окно VK, а наша запись — уже наша забота
  messagesAllowed.value = true

  try {
    await allowGroupMessages()
  } catch {
    // Разрешение в VK выдано, а наша запись не прошла — сети не было или
    // сессия истекла. Человеку врать не о чем: у него всё сработало,
    // а запись восстановится при следующем нажатии
  }
}

/** Погасить разрешение писать. В VK при этом ничего не меняется — отменить
 *  выданное разрешение Bridge не умеет вовсе, — но рассылка берёт получателей
 *  по нашему флагу, и писать мы перестаём сразу.
 *  Бросает Error, если запрос не прошёл: флаг остался поднятым, и обещать
 *  человеку тишину было бы неправдой. */
async function deny() {
  try {
    await denyGroupMessages()
  } catch {
    throw new Error('Не получилось отключить. Попробуй ещё раз.')
  }

  messagesAllowed.value = false
}

/** Доступ к состоянию сообщества из компонентов. Всем отдаёт одно и то же. */
export function useGroup() {
  return {
    status,
    joined,
    messagesAllowed,
    // Показывать ли блок вообще. Условие одно — сообщество настроено: пустой
    // VK_GROUP_ID на сервере отдаёт group_id: null, и кнопки молча ничего не
    // делали бы. Вне VK сервер отдаёт null тоже, так что в вебе блока нет
    available: computed(() => groupId.value !== null),
    // Можно ли открывать окна VK. Отдельно от available потому, что окна
    // нужны не всем кнопкам: «не присылать» гасит наш же флаг и работает
    // везде, а «подписаться» и «присылать» без Bridge открыть нечего.
    // Приложение легко оказывается вне Mini App: адрес с sign= остаётся в
    // истории браузера и пускает внутрь, а Bridge там мёртв
    canPrompt: computed(() => platform.supportsGroup()),
    ensureLoaded,
    join,
    allow,
    deny,
  }
}
