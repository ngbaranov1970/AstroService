<script setup>
// Блок сообщества VK: подписаться и разрешить присылать гороскоп дня в личные
// сообщения. Показывают его конец готового разбора (ReportView) и профиль.
// Данных снаружи не принимает — всё берёт из useGroup, общего на приложение.
//
// Окна VK открывает только Bridge, а он жив лишь внутри Mini App — отсюда
// canPrompt. Без него остаётся кнопка отказа: она гасит наш флаг рассылки и
// работает хоть в браузере. Консоли в мобильном приложении VK нет, поэтому
// каждый отказ выводится подписью под кнопками — молчащая кнопка неотличима
// от сломанного экрана.

import { onMounted, ref } from 'vue'

import { useGroup } from '@/shared/composables/useGroup'

const {
  status,
  joined,
  enabled,
  needsPrompt,
  available,
  canPrompt,
  ensureLoaded,
  join,
  allow,
  deny,
} = useGroup()

// Подпись под кнопками: и «открываем окно VK…», и текст отказа.
// Пустая строка — подписи нет
const note = ref('')

// Какая кнопка сейчас нажата: 'join' | 'allow' | 'deny' | null. Одной
// переменной вместо трёх флагов — нажать можно только что-то одно
const busy = ref(null)

// onMounted — «когда компонент появился на экране». Тут спрашиваем состояние;
// ensureLoaded сходит в сеть один раз за запуск, второй экран получит готовое
onMounted(ensureLoaded)

/** Общая обёртка для трёх кнопок: блокировка, подпись, разбор ошибки.
 *  action — функция из useGroup, success — что написать при удаче. */
async function run(name, action, success) {
  busy.value = name
  // Подпись меняем ДО вызова VK: она доказывает, что нажатие дошло до кода.
  // Не появился текст — сломан сам экран, а не метод VK
  note.value = 'Открываем окно VK…'

  try {
    await action()
    note.value = success
  } catch (failure) {
    // Сюда попадает и нажатие «Отмена» в окне VK — это не поломка,
    // поэтому фразы в useGroup нейтральные
    note.value = failure.message
  } finally {
    busy.value = null
  }
}

const onJoin = () => run('join', join, 'Готово, ты подписан.')
const onAllow = () => run('allow', allow, 'Спасибо! Гороскоп дня будет приходить в сообщения.')
const onDeny = () => run('deny', deny, 'Больше не пишем. Включить обратно можно этой же кнопкой.')
</script>

<template>
  <!-- v-if с двумя условиями: блок есть только там, где кнопки работают, и
       только когда состояние уже спрошено. Пока идёт запрос, не рисуем ничего —
       мигание карточки в конце разбора заметнее, чем её отсутствие -->
  <section
    v-if="available && status === 'ready'"
    class="rounded-card border border-line-dim bg-bg-card p-5"
  >
    <h2 class="font-display text-title text-ink">Гороскоп дня</h2>

    <!-- Текст зависит от того, что человеку осталось сделать. v-if/v-else-if/
         v-else — ветвление прямо в разметке, как if/elif/else -->
    <p class="mt-2 font-read text-lead text-ink-muted">
      <template v-if="enabled">
        Присылаем каждое утро в личные сообщения ВКонтакте.
      </template>
      <template v-else>
        Разбор дня по твоей карте — каждое утро в личных сообщениях ВКонтакте.
      </template>
    </p>

    <div class="mt-5 space-y-2">
      <!-- Подписка нужна не всем: joined === false — точно не подписан,
           null — выяснить не удалось, и кнопку показываем на всякий случай.
           Подписанному (true) её не рисуем вовсе. Вне Mini App прячем совсем:
           окно подписки открывать нечем -->
      <button
        v-if="canPrompt && joined !== true"
        type="button"
        :disabled="busy !== null"
        class="w-full cursor-pointer rounded-card border border-line-gold px-6 py-4 font-sans text-label tracking-label text-gold uppercase transition-colors active:bg-gold/10 disabled:border-line-dim disabled:text-gold-dim"
        @click="onJoin"
      >
        Подписаться на сообщество
      </button>

      <!-- Отказ выглядит скромнее согласия намеренно: это выход, а не
           равнозначный выбор. Рамки нет, только приглушённая подпись.
           Идёт первой ветвью и без canPrompt: окна VK ей не нужны, а
           отписаться человек должен мочь из любого окружения -->
      <button
        v-if="enabled"
        type="button"
        :disabled="busy !== null"
        class="w-full cursor-pointer px-6 py-3 font-sans text-label tracking-label text-gold-dim uppercase transition-colors active:text-gold disabled:text-line-dim"
        @click="onDeny"
      >
        Не присылать
      </button>

      <!-- Главная кнопка блока: ради разрешения писать всё и затевалось.
           После согласия на её месте появляется обратная — «не присылать».
           Окно VK нужно только тому, кто разрешения ещё не давал, — остальным
           кнопка доступна везде, в том числе в браузере -->
      <button
        v-else-if="canPrompt || !needsPrompt"
        type="button"
        :disabled="busy !== null"
        class="w-full cursor-pointer rounded-card border border-gold px-6 py-4 font-sans text-label tracking-label text-gold uppercase transition-colors active:bg-gold/10 disabled:border-line-dim disabled:text-gold-dim"
        @click="onAllow"
      >
        Присылать гороскоп дня
      </button>

      <!-- Приложение открыто вне VK: включить рассылку отсюда нельзя, окно
           разрешения показывает сам ВКонтакте. Кнопку в этом случае не рисуем
           вовсе — нажатие ушло бы в никуда и выглядело поломкой -->
      <p v-else class="font-sans text-meta text-ink-muted">
        Включить можно только из приложения внутри ВКонтакте — открой его из нашего сообщества.
      </p>
    </div>

    <!-- Подпись о том, что произошло. Единственный способ сказать человеку об
         отказе: консоли в мобильном клиенте VK нет -->
    <p v-if="note" class="mt-3 font-sans text-meta text-ink-muted">{{ note }}</p>
  </section>
</template>
