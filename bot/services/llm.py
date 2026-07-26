"""Клиент DeepSeek: единственное место, где бот обращается к LLM."""

import asyncio
import logging
from datetime import datetime

from openai import AsyncOpenAI

from config import TIMEZONE, settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Ты — опытный астролог, пишешь гороскопы на русском языке.\n"
    "Обязательно:\n"
    "— на весь текст 1-2 конкретных образа или метафоры (природа, свет, "
    "движение, погода), не в каждом предложении подряд; остальные фразы — "
    "простые и прямые, без нагромождения сравнений;\n"
    "— советы должны быть конкретными и практическими, а не абстрактными "
    "пожеланиями вроде «будьте открыты новому»;\n"
    "— переходи между темами (настрой, работа, самочувствие, любовь) плавно, "
    "через связки или общий сюжет дня, а не перечисляй их как отдельные пункты;\n"
    "— текст должен быть написан именно для этого знака, "
    "а не подошёл бы любому.\n"
    "Избегай общих мистических фраз, которые не несут конкретного смысла "
    "и подошли бы любому дню и любому знаку (вроде «звёзды благосклонны» "
    "или «вселенная посылает знаки») — но лёгкая поэтичность и метафоры уместны, "
    "если они конкретны.\n"
    "Пиши связным текстом, без заголовков, списков и markdown-разметки.\n"
    "Никогда не упоминай, что ты ИИ или языковая модель."
)

USER_PROMPT = (
    "Напиши гороскоп для знака {sign} {period_phrase}. "
    "Начни текст ровно со слов «{opener}...», не меняя эту формулировку. "
    "Затронь общий настрой, работу, финансы, самочувствие, любовь. "
    "Объём: {length}. Заверши одной короткой рекомендацией."
)

# period -> (формулировка периода для промпта, желаемый объём, открывающие слова текста).
# {today} обязателен в каждой формулировке: без него модель не знает
# текущую дату и додумывает период сама (например, называет случайный месяц).
PERIOD_PROMPTS: dict[str, tuple[str, str, str]] = {
    "day": ("на сегодня, {today}", "4–6 предложений", "Сегодня"),
    "week": ("на текущую неделю (сегодня {today})", "6–8 предложений", "На этой неделе"),
    "month": ("на {month_name} {year} года — текущий месяц", "8–10 предложений", "В этом месяце"),
    "year": ("на текущий год (сегодня {today})", "10–12 предложений", "В этом году"),
}

# Номер месяца -> название в именительном падеже, для {month_name} в PERIOD_PROMPTS["month"].
# date.today().strftime("%B") зависит от системной локали, поэтому не полагаемся на неё.
MONTH_NAMES = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)

MAX_ATTEMPTS = 3

# Лимит сообщения Telegram — 4096 символов, а рассуждающая модель иногда
# игнорирует "объём" из промпта и пишет заметно больше. Запас оставлен
# под HTML-заголовок (эмодзи, знак, период), который добавляется поверх.
MAX_HOROSCOPE_LENGTH = 3500


def truncate_horoscope(text: str, limit: int = MAX_HOROSCOPE_LENGTH) -> str:
    if len(text) <= limit:
        return text
    cut = text.rfind(".", 0, limit)
    if cut == -1:
        return text[:limit].rstrip() + "…"
    return text[: cut + 1]


class LLMClient:
    def __init__(self) -> None:
        self._client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_api_url,
        )

    async def generate_horoscope(self, sign_name: str, period: str) -> str:
        today = datetime.now(TIMEZONE).date()
        period_phrase, length, opener = PERIOD_PROMPTS[period]
        prompt = USER_PROMPT.format(
            sign=sign_name,
            period_phrase=period_phrase.format(
                today=today.isoformat(),
                month_name=MONTH_NAMES[today.month - 1],
                year=today.year,
            ),
            length=length,
            opener=opener,
        )
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = await self._client.chat.completions.create(
                    model=settings.deepseek_model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    # deepseek-v4-pro — рассуждающая модель: температуру игнорирует,
                    # а из max_tokens тратит ~1000+ на внутренние рассуждения
                    max_tokens=4000,
                )
                content = response.choices[0].message.content
                if not content:
                    raise ValueError("DeepSeek вернул пустой ответ")
                content = content.strip()
                if not content:
                    raise ValueError("DeepSeek вернул только пробелы")
                return truncate_horoscope(content)
            except Exception as exc:
                logger.warning(
                    "DeepSeek: попытка %d/%d не удалась: %s",
                    attempt, MAX_ATTEMPTS, exc,
                )
                if attempt == MAX_ATTEMPTS:
                    raise
                await asyncio.sleep(2 ** (attempt - 1))
        raise RuntimeError("unreachable")
