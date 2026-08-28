"""Публичные страницы: витрина, оферта, политика ПДн, реквизиты.

Нужны двум проверяющим — модерации ЮKassa (без оферты, фиксированной цены и
реквизитов магазин не подключают) и модерации каталога VK. Поэтому здесь нет
ни `current_user`, ни сессии: заход проверяющего не должен ни заводить строку
в `users`, ни упираться в 403 из-за ссылки без токена VK.

Реквизиты продавца лежат константами здесь, а не в настройках: они публичны
по своей сути и меняются раз в жизнь, а `.env` пришлось бы править на
сервере ради строки, которая и так напечатана на странице.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from starlette.responses import Response

from app.models import ReportType
from app.services import orders
from app.web import deps
from app.web.templating import templates

MERCHANT = {
    "name": "Баранов Николай Геннадьевич",
    "status": "Самозанятый, плательщик налога на профессиональный доход",
    "inn": "620400375525",
    "email": "ngbaranov@gmail.com",
    "phone": "+7 958 190-87-88",
    "site": "astrozvezdochet.ru",
}

# Реквизиты печатаются в подвале КАЖДОЙ страницы, поэтому глобал, а не
# переменная контекста: передавать их в шесть шаблонов стенда пришлось бы
# руками, и первый же новый шаблон вышел бы без ИНН.
templates.env.globals["merchant"] = MERCHANT

# Цена платного разбора в рублях — из прайса services/orders.py, а не своя
# константа: по этой же цифре выставляется счёт в ЮKassa, и разойдись они,
# человек увидит в оферте одно, а в форме оплаты другое.
#
# Фиксированная цена — требование ЮKassa: «работаю без фиксированных цен»
# уводит на приём платежей по счетам, где вебхука нет и открывать отчёт
# автоматически нечем.
PRICE_RUB = orders.price_rub(ReportType.FULL_NATAL)

# Тоже глобал: цену называет не только /pricing, но и анонс полного разбора
# на странице карты. Разойдись они — на модерации это читается как «цена на
# сайте не та, что в оферте». Глобалом, а не импортом в router.py: роутер
# отдаёт этому модулю templates, обратный импорт замкнул бы круг.
templates.env.globals["price_rub"] = PRICE_RUB

# Дата редакции оферты и политики. Меняется вместе с текстом: человек должен
# видеть, ту ли версию он принимал.
REVISION_DATE = "10 августа 2026 г."

router = APIRouter(include_in_schema=False)


def _page(request: Request, template: str) -> Response:
    """Отрисовать страницу с общим для всех четырёх набором данных."""
    return templates.TemplateResponse(
        request,
        template,
        {"merchant": MERCHANT, "price": PRICE_RUB, "revision": REVISION_DATE},
    )


# Две страницы про покупку закрыты для VK: ссылок на них оттуда нет, но
# адрес набирается руками, а прайс внутри Mini App — то же нарушение 5.4.1,
# что и кнопка оплаты. Политика и контакты остаются открытыми всегда:
# первую требует каталог, вторую — п. 2.4.1 правил (способ связи).
@router.get("/pricing", dependencies=[Depends(deps.forbid_in_vk)])
async def pricing(request: Request) -> Response:
    """Витрина: что продаём, за сколько и как покупатель получает заказ."""
    return _page(request, "pricing.html")


@router.get("/offer", dependencies=[Depends(deps.forbid_in_vk)])
async def offer(request: Request) -> Response:
    """Публичная оферта — договор, который человек принимает оплатой."""
    return _page(request, "offer.html")


@router.get("/privacy")
async def privacy(request: Request) -> Response:
    """Политика обработки персональных данных (152-ФЗ)."""
    return _page(request, "privacy.html")


@router.get("/contacts")
async def contacts(request: Request) -> Response:
    """Реквизиты и связь. Отдельным адресом: анкета ЮKassa просит ссылку."""
    return _page(request, "contacts.html")
