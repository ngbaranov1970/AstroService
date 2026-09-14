<script setup>
// Шаг 3: город рождения. Показывает его OnboardingView. Отдаёт наверх не
// текст, а выбранную подсказку {label, lat, lon} — из неё сервер получает
// координаты, по которым считает часовой пояс. Пока подсказка не выбрана,
// модель равна null, и мастер не пускает дальше.

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { searchPlaces } from '@/shared/api/endpoints'
import AppInput from '@/shared/ui/AppInput.vue'

const model = defineModel({ type: Object, default: null })

// Набранный текст живёт здесь, а не в форме мастера: наверх он не нужен,
// туда уезжает только выбранный город
const query = ref(model.value?.label ?? '')
const options = ref([])
const searching = ref(false)
const failed = ref(false)

// Служебные переменные без ref: шаблон их не показывает, следить незачем
let timer = null
let controller = null
// Выбор из списка сам подставляет название в поле — этот один прогон
// наблюдателя пропускаем, иначе он тут же сбросит только что сделанный выбор
let ignoreNextChange = false

/** Отменить и запланированный, и уже летящий запрос. */
function stopSearch() {
  clearTimeout(timer)
  // abort() у завершённого запроса безвреден, проверять состояние не нужно
  controller?.abort()
  controller = null
}

async function runSearch(text) {
  // AbortController — пульт отмены запроса. Держим ссылку на свой, чтобы
  // отличить «этот запрос отменили» от «пришёл ответ на актуальный ввод»
  const own = new AbortController()
  controller = own

  try {
    options.value = await searchPlaces(text, { signal: own.signal })
  } catch (failure) {
    // Отмену ради нового ввода ошибкой не считаем: экран уже ищет другое
    if (failure.name === 'AbortError') return
    options.value = []
    failed.value = true
  } finally {
    // Гасим «Ищем…» только если это был последний запрос: иначе отменённый
    // предшественник погасит индикатор уже идущего поиска
    if (controller === own) searching.value = false
  }
}

// watch(источник, обработчик) — «выполняй это при каждом изменении».
// В Python аналога нет: там сравнивали бы значения руками
watch(query, (value) => {
  if (ignoreNextChange) {
    ignoreNextChange = false
    return
  }

  // Текст правят — прежний выбор больше не действителен, иначе уехали бы
  // координаты Ростова с подписью «Ростов-на-Дону»
  model.value = null
  stopSearch()
  failed.value = false

  const text = value.trim()
  // По одной букве ищем весь мир: ждём хотя бы двух
  if (text.length < 2) {
    options.value = []
    searching.value = false
    return
  }

  searching.value = true
  // Задержка перед запросом (debounce): человек печатает быстрее, чем летит
  // ответ, и без паузы на «Ростов» ушло бы шесть запросов вместо одного
  timer = setTimeout(() => runSearch(text), 300)
})

function choose(place) {
  model.value = place
  ignoreNextChange = true
  query.value = place.label
  options.value = []
  stopSearch()
}

// Уход с шага не должен оставлять за собой таймер и летящий запрос
onBeforeUnmount(stopSearch)

const nothingFound = computed(
  () => !model.value && !searching.value && !failed.value && query.value.trim().length >= 2,
)
</script>

<template>
  <div class="w-full text-center">
    <h1 class="font-display text-display text-ink">Где вы родились?</h1>
    <p class="mt-3 font-display text-lead text-ink-muted italic">
      По городу определим координаты и часовой пояс — тот, что действовал в
      год рождения
    </p>

    <div class="mt-10">
      <AppInput v-model="query" placeholder="Начните вводить город" autofocus />

      <!-- Подсказки разворачиваются под линией поля. Высоту ограничиваем и
           даём прокрутку списку, а не странице: экран онбординга не скроллится -->
      <p v-if="searching" class="mt-4 text-meta text-ink-muted">Ищем…</p>

      <ul v-else-if="options.length" class="mt-2 max-h-56 overflow-y-auto text-left">
        <li v-for="place in options" :key="`${place.lat},${place.lon}`">
          <button
            type="button"
            class="w-full border-b border-line-dim py-3 font-display text-lead text-ink"
            @click="choose(place)"
          >
            {{ place.label }}
          </button>
        </li>
      </ul>

      <p v-else-if="failed" class="mt-4 text-meta text-ink-muted">
        Не получилось поискать — проверьте связь
      </p>

      <p v-else-if="nothingFound" class="mt-4 text-meta text-ink-muted">
        Ничего не нашли. Попробуйте название покороче
      </p>
    </div>
  </div>
</template>
