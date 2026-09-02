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
// Разрешение канала: сообществу позволено писать. Даётся окном VK один раз
// и живёт до отзыва в самом VK — нашей кнопкой не гасится
const messagesAllowed = ref(false)
// Решение о письмах, отдельное от разрешения: выключил рассылку — разрешение
// осталось, и включить обратно можно без окна VK
const subscribed = ref(false)
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
      subscribed.value = state.subscribed
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

/** Включить рассылку. Окно VK открывает только тем, кто разрешения ещё не
 *  давал: у остальных оно в VK уже есть, и хватает запроса к нам — поэтому
 *  включение работает и вне Mini App.
 *  Бросает Error с фразой для человека, если VK разрешения не дал или наш
 *  запрос не прошёл. */
async function allow() {
  if (!messagesAllowed.value) {
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
  }

  // Второй флаг ставит только сервер: без записи о подписке рассылка человека
  // не возьмёт, и молча показать «включено» было бы обманом
  try {
    await allowGroupMessages()
  } catch {
    throw new Error('Не получилось включить. Попробуй ещё раз.')
  }

  subscribed.value = true
}

/** Выключить рассылку. В VK при этом ничего не меняется — отменить выданное
 *  разрешение Bridge не умеет вовсе, — но рассылка берёт получателей по
 *  нашему флагу, и писать мы перестаём сразу.
 *  Бросает Error, если запрос не прошёл: флаг остался поднятым, и обещать
 *  человеку тишину было бы неправдой. */
async function deny() {
  try {
    await denyGroupMessages()
  } catch {
    throw new Error('Не получилось отключить. Попробуй ещё раз.')
  }

  // Гасим только подписку: разрешение VK остаётся, и включить письма обратно
  // человек сможет одной кнопкой, где бы приложение ни было открыто
  subscribed.value = false
}

/** Доступ к состоянию сообщества из компонентов. Всем отдаёт одно и то же. */
export function useGroup() {
  return {
    status,
    joined,
    // Рассылка включена: нужны оба флага сразу — разрешение канала от VK и
    // согласие человека на письма
    enabled: computed(() => messagesAllowed.value && subscribed.value),
    // Нужно ли для включения окно VK. Разрешение даётся один раз: у того, кто
    // его уже давал, кнопка работает и в браузере
    needsPrompt: computed(() => !messagesAllowed.value),
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
