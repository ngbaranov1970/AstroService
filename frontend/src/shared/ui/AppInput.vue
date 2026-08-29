<script setup>
// Поле-линия из макета: рамки нет, есть крупный текст по центру и золотая
// черта под ним. Используют шаги онбординга (город, имя); дата и время
// рисуют своё поле — там под текстом прячется системный выбор значения.

import { onMounted, useTemplateRef } from 'vue'

// defineModel() — приём для двусторонней связи. Родитель пишет
// v-model="city", здесь читаем и меняем model.value, и значение ходит в обе
// стороны. Под капотом это проп modelValue плюс событие update:modelValue —
// «магии» нет, просто Vue пишет обе половины за нас
const model = defineModel({ type: String, default: '' })

const props = defineProps({
  placeholder: { type: String, default: '' },
  maxlength: { type: Number, default: 64 },
  // inputmode подсказывает мобильной клавиатуре, какую раскладку открыть
  inputmode: { type: String, default: 'text' },
  // Ставить ли курсор в поле сразу: на шаге города это экономит тап
  autofocus: { type: Boolean, default: false },
})

// useTemplateRef('input') — доступ к настоящему DOM-элементу, помеченному
// ref="input" в шаблоне. Нужен там, где Vue не помогает: фокус, выделение,
// системные диалоги. Аналогия с Python неуместна — это чистый браузер
const inputEl = useTemplateRef('input')

onMounted(() => {
  // Клавиатуру в мобильном WebView автофокус может и не открыть — это
  // ограничение системы, а не ошибка: курсор всё равно стоит в поле
  if (props.autofocus) inputEl.value?.focus()
})
</script>

<template>
  <!-- bg-transparent и focus:outline-none снимают всё, что браузер рисует
       сам: своя рамка полю не нужна — полем выглядит золотая черта снизу.
       caret-gold красит мигающий курсор, иначе он останется белым.
       autocomplete/autocorrect выключены: имя человека и город браузер
       норовит подставить из своих сохранённых форм -->
  <input
    ref="input"
    v-model="model"
    type="text"
    :placeholder="placeholder"
    :maxlength="maxlength"
    :inputmode="inputmode"
    autocomplete="off"
    autocapitalize="words"
    spellcheck="false"
    class="w-full border-b border-line-gold bg-transparent pb-3 text-center font-display text-2xl text-ink caret-gold placeholder:text-ink-muted/50 focus:outline-none"
  />
</template>
