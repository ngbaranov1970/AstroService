<script setup>
// Возврат с оплаты: чем кончился платёж и вход в купленный разбор.
// Показывает роутер по адресу /paid/:token?order=<токен заказа>, приводит
// сюда редиректом серверная страница /pay/<token>/done — она уже сверила
// платёж с ЮKassa и поставила текст в очередь (backend/app/web/routes/pay.py).
//
// Сам возврат человека ничего не доказывает: вкладку закрывают на полпути,
// а зачисление идёт своим темпом. Поэтому экран не объявляет оплату сам, а
// спрашивает состояние заказа у сервера, пока тот не ответит «оплачено».

import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { getOrder } from '@/shared/api/endpoints'
import { usePolling } from '@/shared/composables/usePolling'
import { useReports } from '@/shared/composables/useReports'
import AppButton from '@/shared/ui/AppButton.vue'

// К чему относится купленный разбор. Смотрим на тип из заказа, а не на
// маршрут: адрес возврата у сервера один на карту и пару (/paid/<token>),
// и в токене из адреса не написано, чей он
const SCOPE_BY_TYPE = Object.freeze({
  full_natal: 'chart',
  pair_full: 'pair',
})

const route = useRoute()
const router = useRouter()

const token = computed(() => String(route.params.token ?? ''))
// Токен заказа приходит в адресе от страницы возврата. Пусто — сюда зашли
// руками, показывать нечего
const orderToken = computed(() => String(route.query.order ?? ''))

const order = ref(null)
// Последний запрос не удался (сеть, недоступный API ЮKassa). Не «не оплачено»:
// деньги в этот момент могут быть уже зачислены
const failed = ref(false)

const paid = computed(() => order.value?.paid === true)
// Ничего ещё не знаем: первый ответ не пришёл, а ошибки не было
const isLoading = computed(() => order.value === null && !failed.value)

// Пока заказ не пришёл, разборы перечитывать нечему: тип неизвестен, а
// гадать нельзя — токен из адреса может оказаться и парой, и картой
const scope = computed(() => SCOPE_BY_TYPE[order.value?.report_type] ?? null)
// useReports зовём внутри computed: scope известен не сразу, а с ответом
const api = computed(() => (scope.value ? useReports(scope.value) : null))

/** Спросить состояние заказа. Сервер при этом сверяется с ЮKassa и, если
 *  деньги на месте, ставит разбор в очередь. */
async function refresh() {
  if (!orderToken.value) return

  try {
    const fresh = await getOrder(orderToken.value)
    order.value = fresh
    failed.value = false
    // Разбор только что открылся — список разборов на других экранах об этом
    // ещё не знает, и без перечитывания там остался бы «заперт»
    if (fresh.paid && api.value) await api.value.reload(token.value)
  } catch {
    // catch без переменной: какая именно ошибка, экрану неважно — текст один
    failed.value = true
  }
}

// Пока не оплачено, спрашиваем раз в пять секунд: зачисление обычно приходит
// за секунды, но возврат с формы иногда опережает его. Три минуты — потолок,
// дальше ждать бессмысленно, деньги подтвердит вебхук и без нас
const waiting = computed(() => Boolean(orderToken.value) && !paid.value)
const { timedOut } = usePolling(refresh, waiting, { intervalMs: 5000 })

/** Открыть купленный разбор.
 *
 *  Заказа нет (адрес открыли руками либо сервер не ответил) — уводим на
 *  главную: какой именно разбор куплен, известно только из заказа, а увести
 *  в несуществующий значит показать «такого разбора пока нет». */
function goToReport() {
  if (!order.value) {
    router.push({ name: 'home' })
    return
  }

  router.push({
    name: scope.value === 'pair' ? 'pair-report' : 'report',
    params: { token: token.value, type: order.value.report_type },
  })
}

// Подпись кнопки выхода: без заказа она ведёт на главную, и обещать разбор
// было бы враньём
const exitLabel = computed(() => (order.value ? 'К разбору' : 'На главную'))

// Дата и время оплаты — то, что человек сверит с выпиской банка
const paidAt = computed(() => {
  if (!order.value?.paid_at) return ''

  return new Intl.DateTimeFormat('ru-RU', {
    day: 'numeric',
    month: 'long',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(order.value.paid_at))
})

// Первый запрос делаем сами: опрос зовёт функцию только через интервал, а
// ответ нужен сразу — обычно к этому моменту всё уже оплачено
watch(
  orderToken,
  (value) => {
    if (value) refresh()
  },
  { immediate: true },
)
</script>

<template>
  <!-- Шапки с «назад» здесь нет намеренно: назад отсюда — форма оплаты на
       чужом домене, возвращаться туда незачем. Выход один — в разбор -->
  <div class="flex flex-1 flex-col items-center justify-center gap-4 text-center">
    <!-- 1. Адрес открыли руками, без номера заказа -->
    <template v-if="!orderToken">
      <h1 class="font-display text-title text-ink">Оплата не начиналась</h1>
      <p class="font-read text-lead text-ink-muted">
        Похоже, эта страница открыта напрямую. Заказ оформляется на странице разбора.
      </p>
      <div class="mt-2 w-full">
        <AppButton @click="goToReport">{{ exitLabel }}</AppButton>
      </div>
    </template>

    <!-- 2. Первый ответ ещё не пришёл -->
    <p v-else-if="isLoading" class="animate-pulse font-display text-title text-ink-muted">
      Проверяем оплату…
    </p>

    <!-- 3. Деньги на месте — ради этой ветки всё и затевалось -->
    <template v-else-if="paid">
      <h1 class="font-display text-display text-ink">Оплачено</h1>
      <p class="font-read text-lead text-ink-muted">
        {{ order.title }} уже готовится — это займёт около минуты.
      </p>

      <div class="mt-2 w-full">
        <AppButton @click="goToReport">Открыть разбор</AppButton>
      </div>

      <!-- Номер заказа — единственное, что покупатель может назвать при
           обращении: контактов мы не спрашиваем -->
      <p class="mt-4 font-sans text-meta text-ink-muted">
        Заказ №{{ order.number }}{{ paidAt ? ` от ${paidAt}` : '' }} · {{ order.price_rub }} ₽
      </p>
      <!-- Чек НПД выписывается вручную (решение 2026-08-13): письмо от
           платёжного сервиса чеком не является, и человек должен это знать -->
      <p class="font-sans text-meta text-ink-muted">
        Чек самозанятого придёт отдельно — письмо об оплате чеком не является.
      </p>
    </template>

    <!-- 4. Заказ есть, денег ещё не видно (или спросить не удалось).
         Утверждать «не оплачено» нельзя: платёж мог пройти секунду назад -->
    <template v-else>
      <h1 class="font-display text-title text-ink">Проверяем оплату</h1>
      <p class="font-read text-lead text-ink-muted">
        Если платёж прошёл, разбор откроется в течение минуты. Приложение можно
        закрыть — ничего не потеряется.
      </p>

      <!-- Опрос кончился ничем: дальше человеку нужен способ уйти -->
      <p v-if="timedOut" class="font-sans text-meta text-ink-muted">
        Ответа от платёжного сервиса всё ещё нет. Если деньги списались, разбор
        откроется сам — загляни чуть позже.
      </p>

      <div class="mt-2 w-full">
        <AppButton @click="goToReport">{{ exitLabel }}</AppButton>
      </div>

      <p v-if="order" class="mt-4 font-sans text-meta text-ink-muted">
        Заказ №{{ order.number }} · {{ order.price_rub }} ₽. Назови этот номер, если
        деньги списались, а разбор так и не открылся.
      </p>
    </template>
  </div>
</template>
