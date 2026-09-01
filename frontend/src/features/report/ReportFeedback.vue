<script setup>
// Оценка разбора: пять звёзд и необязательный комментарий. Показывает её
// ReportView под готовым текстом; принимает token карты и тип разбора.
//
// Отправка одна на всё: звёзды выбираются локально, запрос уходит по кнопке.
// Иначе тап по звезде и последующий комментарий дали бы два события FEEDBACK,
// и в аналитике один человек считался бы дважды.
//
// Оценку спрашиваем и у того, кому переслали ссылку: владельцем карты быть не
// обязательно (бэкенд различает их флагом own в самом событии).

import { computed, ref } from 'vue'

import { leaveFeedback } from '@/shared/api/endpoints'
import AppButton from '@/shared/ui/AppButton.vue'

const props = defineProps({
  token: { type: String, required: true },
  type: { type: String, required: true },
})

const STARS = [1, 2, 3, 4, 5]

// Потолок длины тот же, что MAX_COMMENT в backend/app/services/reports.py:
// сервис молча обрежет лишнее, и лучше не давать написать то, что пропадёт
const MAX_COMMENT = 500

const rating = ref(0) // 0 — ещё не выбрано
const comment = ref('')
const sending = ref(false)
const sent = ref(false)
const error = ref('')

// Плейсхолдер поля зависит от оценки: у недовольного спрашиваем, что не так,
// у довольного — что понравилось. Один и тот же вопрос обоим звучит глухо
const placeholder = computed(() =>
  rating.value <= 3 ? 'Что не так? (необязательно)' : 'Что понравилось? (необязательно)',
)

/** Отправить оценку. Ответ пустой (204), поэтому смотрим только на отказ. */
async function send() {
  sending.value = true
  error.value = ''

  try {
    await leaveFeedback(props.token, props.type, rating.value, comment.value)
    sent.value = true
  } catch {
    // Текст один на все случаи: человеку всё равно, 500 это или обрыв сети,
    // а сделать он может только одно — нажать ещё раз
    error.value = 'Не получилось отправить. Попробуй ещё раз.'
  } finally {
    sending.value = false
  }
}
</script>

<template>
  <section class="border-t border-line-dim pt-6">
    <!-- После отправки блок сменяется благодарностью: оценка живёт до
         перезагрузки экрана, второй раз в ту же сессию не спрашиваем -->
    <p v-if="sent" class="font-read text-lead text-ink-muted">
      Спасибо! Оценка записана — по ней мы правим тексты.
    </p>

    <template v-else>
      <h2 class="font-sans text-label tracking-label text-gold-dim uppercase">
        Насколько похоже на тебя?
      </h2>

      <!-- Звёзды. Кнопка на каждую, а не один <input type="range">: пять
           крупных целей пальцем надёжнее ползунка в вебвью -->
      <div class="mt-4 flex justify-center gap-3">
        <button
          v-for="star in STARS"
          :key="star"
          type="button"
          class="cursor-pointer p-1 text-3xl leading-none transition-colors"
          :class="star <= rating ? 'text-gold' : 'text-line-dim'"
          @click="rating = star"
        >
          <!-- Символ один и тот же, отличается только цветом: два разных знака
               (★ и ☆) в шрифтах телефона имеют разную ширину и прыгают -->
          ★
        </button>
      </div>

      <!-- Комментарий и кнопка появляются только после выбора звезды: до него
           отправлять нечего, а пустое поле внизу разбора выглядит требованием -->
      <template v-if="rating > 0">
        <!-- textarea, а не input: жалоба на разбор редко умещается в строку.
             v-model связывает поле с переменной в обе стороны -->
        <textarea
          v-model="comment"
          :placeholder="placeholder"
          :maxlength="MAX_COMMENT"
          rows="3"
          class="mt-5 w-full resize-none rounded-card border border-line-dim bg-transparent p-4 font-read text-lead text-ink caret-gold placeholder:text-ink-muted/60 focus:border-line-gold focus:outline-none"
        />

        <div class="mt-3">
          <AppButton :loading="sending" loading-text="Отправляем…" @click="send">
            Отправить
          </AppButton>
        </div>
      </template>

      <p v-if="error" class="mt-3 font-sans text-meta text-ink-muted">{{ error }}</p>
    </template>
  </section>
</template>
