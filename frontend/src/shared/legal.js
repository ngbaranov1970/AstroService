// Юридические страницы сайта: адреса, подписи и правило «в VK про покупку не
// говорим». Единственное место фронта, которое знает эти пути.
// Зовут его экран оплаты и профиль, сам он ходит только в адаптер платформы.

import { platform } from '@/app/platform'

// Страницы отдаёт FastAPI (backend/app/web/routes/legal.py), а не Vue, и так
// останется после отказа от стенда: их открывают модераторы ЮKassa и VK
// прямой ссылкой, без запуска приложения и без нашей сессии.
//
// vkSafe: false — страница про покупку, внутри Mini App показывать нельзя
// (п. 5.4.1 правил VK; на сервере тот же запрет ставит forbid_in_vk)
export const LEGAL_PAGES = [
  { path: '/pricing', label: 'Услуги и цены', vkSafe: false },
  { path: '/offer', label: 'Оферта', vkSafe: false },
  { path: '/privacy', label: 'Обработка данных', vkSafe: true },
  { path: '/contacts', label: 'Контакты', vkSafe: true },
]

/** Ссылки, разрешённые на текущей платформе. Возвращает подмножество
 *  LEGAL_PAGES: в VK — две, в вебе и Telegram — все четыре. */
export function visibleLegalPages() {
  // filter оставляет элементы, на которых функция вернула истину, — как
  // [x for x in pages if x.vkSafe] в Python
  if (platform.name === 'vk') return LEGAL_PAGES.filter((page) => page.vkSafe)

  return LEGAL_PAGES
}

/** Открыть юридическую страницу поверх приложения (новой вкладкой). */
export function openLegal(path) {
  // new URL(путь, база) достраивает относительный путь до полного адреса —
  // '/offer' превращается в 'https://astrozvezdochet.ru/offer'. Делаем это
  // потому, что в VK приложение живёт в iframe, и относительный адрес разные
  // клиенты открывают по-разному; .href — готовая строка из этого объекта
  platform.openExternal(new URL(path, window.location.origin).href)
}
