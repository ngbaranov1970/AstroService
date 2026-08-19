// Выбор платформы: где мы запущены — VK, Telegram или обычный браузер.
// Весь остальной код импортирует ТОЛЬКО этот файл и работает с platform,
// не зная, какой адаптер за ним стоит.

import { telegramPlatform } from './telegram'
import { vkPlatform } from './vk'
import { webPlatform } from './web'

function detect() {
  const params = new URLSearchParams(window.location.search)
  // Подпись и идентификатор вместе — так выглядит только запуск из VK.
  // По одному vk_platform судить нельзя: его легко дописать в адрес руками
  if (params.get('sign') && params.get('vk_user_id')) return vkPlatform

  if (window.Telegram?.WebApp?.initData) return telegramPlatform

  return webPlatform
}

// Определяем один раз при загрузке модуля: платформа за время работы
// приложения не меняется, а параметры запуска из адреса ещё не стёрты
export const platform = detect()
