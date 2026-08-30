<script setup>
// Пунктирная карточка-анонс: курсив по центру, рамка штрихами.
// Первым её показывает HomeView внизу списка плиток; текст приходит содержимым.
// Лежит в shared/ui, а не в features/home: тот же анонс нужен на экране
// «скоро» и в разборах — форма одна, текст всегда разный.
</script>

<template>
  <!-- border-dashed — пунктир вместо сплошной линии. Рамка самая тусклая на
       экране: это обещание, а не то, что можно нажать (клика у карточки нет) -->
  <div class="rounded-card border border-dashed border-line-dim px-5 py-6 text-center">
    <p class="font-display text-lead text-ink-muted italic">
      <slot />
    </p>
  </div>
</template>
