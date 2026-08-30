<script setup>
// Карточка «Разобрать карту близкого»: единственный видимый вход за второй
// картой. Показывает её HomeView сразу за плитками разборов — там, где
// человек уже прочитал свой разбор и понимает, что получит.
// Данных не принимает: это ссылка на онбординг и три строки текста.
//
// Пунктир, как у DashedCard-анонса, но золотой и с капс-действием: тусклая
// рамка того компонента означает «обещание, нажимать нечего», а здесь наоборот.
</script>

<template>
  <!-- RouterLink рисуется обычной ссылкой <a>, поэтому её видно как ссылку и
       клавиатуре, и чтению с экрана. block растягивает её на всю ширину:
       ссылка по умолчанию занимает ровно свой текст -->
  <RouterLink
    :to="{ name: 'onboarding' }"
    class="block rounded-card border border-dashed border-line-gold bg-bg-card px-5 py-5 transition-colors active:bg-gold/5"
  >
    <p class="font-display text-title text-ink">Разобрать карту близкого</p>
    <p class="mt-1 font-sans text-meta text-ink-muted">
      Мама, партнёр, ребёнок — свой разбор для каждого
    </p>
    <p class="mt-4 font-sans text-label tracking-label text-gold uppercase">Ввести данные →</p>
  </RouterLink>
</template>
