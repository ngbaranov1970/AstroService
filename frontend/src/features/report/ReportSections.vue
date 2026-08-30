<script setup>
// Готовый текст разбора: заголовки секций и абзацы. Показывает его ReportView,
// когда status = ready; принимает sections из ReportOut — [{title, body}].
//
// Своей логики, кроме оформления текста, здесь нет: компонент ничего не
// запрашивает и ничего не хранит. Заголовки `##` с бэкенда уже сняты
// (parse() в app/llm/prompts/), а `**жирный**` остаётся — его дорисовываем мы.

import { computed } from 'vue'

const props = defineProps({
  // Массив секций; у бесплатного разбора первая может прийти без заголовка
  sections: { type: Array, default: () => [] },
})

// Кусок **в звёздочках**. Флаг g — искать все вхождения, а не первое:
// без него matchAll ниже бросает ошибку. Скобки (.+?) запоминают текст
// внутри звёздочек, `?` делает поиск нежадным — иначе одно совпадение
// растянулось бы от первых звёздочек до последних в абзаце
const BOLD = /\*\*(.+?)\*\*/g

/** Абзац → куски [{text, bold}] для шаблона. Разметку не вставляем: текст
 *  пришёл от модели, и v-html сделал бы тегом любую скобку из него. */
function splitBold(paragraph) {
  const chunks = []
  let done = 0 // сколько символов абзаца уже разложено по кускам

  // matchAll отдаёт все совпадения; match.index — позиция начала совпадения,
  // match[0] — вся находка со звёздочками, match[1] — то, что в скобках
  for (const match of paragraph.matchAll(BOLD)) {
    if (match.index > done) {
      // slice(a, b) — кусок строки, как paragraph[a:b] в Python
      chunks.push({ text: paragraph.slice(done, match.index), bold: false })
    }
    chunks.push({ text: match[1], bold: true })
    done = match.index + match[0].length
  }

  // Хвост после последнего жирного куска; когда звёздочек не было вовсе —
  // это и есть весь абзац одним куском
  if (done < paragraph.length) chunks.push({ text: paragraph.slice(done), bold: false })

  return chunks
}

// computed — значение, пересчитываемое само при смене того, от чего зависит
// (здесь props.sections). Разбор текста делаем один раз на приход данных, а не
// на каждую перерисовку шаблона
const parts = computed(() =>
  props.sections.map((section) => ({
    // Пустая строка тоже считается «заголовка нет»: free_natal так помечает
    // текст, попавший выше первого `##`
    title: section.title || null,
    // Абзацы модель разделяет переводами строк, но не всегда пустой строкой:
    // режем по одному и более \n подряд и выбрасываем пустые остатки.
    // trim() снимает пробелы по краям — как .strip() в Python
    paragraphs: section.body
      .split(/\n+/)
      .map((line) => line.trim())
      .filter((line) => line.length > 0)
      .map(splitBold),
  })),
)
</script>

<template>
  <!-- Интервалы между секциями крупные: в макете воздух — часть вида.
       :key в v-for — метка элемента для Vue, по ней он понимает, что при
       смене списка переставить, а что перерисовать; здесь порядок секций
       задан сервером и не меняется, поэтому индекс в ключе уместен -->
  <div class="flex flex-col gap-8">
    <section v-for="(part, index) in parts" :key="index">
      <h2 v-if="part.title" class="mb-3 font-display text-title text-gold">
        {{ part.title }}
      </h2>

      <!-- font-read (PT Serif) с крупным межстрочным — типографика длинных
           текстов, та же, что у карточки дня на главной. not-first: —
           «применить ко всем, кроме первого»: отступ разделяет абзацы, а
           перед первым он добавил бы пустоту под заголовком -->
      <p
        v-for="(chunks, line) in part.paragraphs"
        :key="line"
        class="font-read text-read text-ink not-first:mt-4"
      >
        <!-- Каждый абзац — цепочка кусков. <template> вместо <span> вокруг
             v-for: он нужен только чтобы навесить директиву и в разметку
             не попадает, лишних элементов внутри абзаца не появляется -->
        <template v-for="(chunk, position) in chunks" :key="position">
          <strong v-if="chunk.bold" class="font-semibold text-ink">{{ chunk.text }}</strong>
          <template v-else>{{ chunk.text }}</template>
        </template>
      </p>
    </section>
  </div>
</template>
