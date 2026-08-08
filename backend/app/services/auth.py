"""Кто пришёл из VK: проверка параметров запуска и наш сессионный токен.

Две независимые половины, и путать их нельзя. `verify_vk_launch` проверяет
ЧУЖОЕ утверждение о личности: VK подписывает параметры запуска защищённым
ключом приложения, мы пересчитываем подпись и только после этого верим
`vk_user_id`. `issue_session`/`read_session` — НАШ токен на своём ключе:
параметры запуска приходят один раз, при открытии мини-приложения, и во
внутренних ссылках их уже нет — личность дальше едет в `?s=<JWT>`.

Про HTTP и VK Bridge файл не знает: на входе словарь параметров, на выходе
строка. Обоснование схемы — docs/astro-context.md, «В VK личность едет в URL».
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from collections.abc import Mapping
from urllib.parse import urlencode

import jwt

from app.config import get_settings

logger = logging.getLogger(__name__)

# Алгоритм подписи наших токенов. Список передаётся в jwt.decode явно:
# иначе библиотека верит полю `alg` из самого токена, а его пишет тот, кто
# токен прислал, — включая `alg: none` (RFC 8725).
_ALGORITHM = "HS256"

# Сутки: столько живёт сессия в адресе. Больше нельзя — ссылка целиком
# отдаёт сессию тому, кому её переслали (та же цена, что у `/r/<token>`).
# Меньше неудобно: тестер заходит через день и не должен терять свои карты.
SESSION_TTL = 24 * 60 * 60

# Час на то, чтобы обменять параметры запуска на сессию. Своего срока
# годности у подписи VK нет — она валидна вечно, а полный URL с ней оседает
# в логах Caddy и uvicorn. Часа хватает на один сеанс: дальше личность несёт
# JWT, и повторно параметры нужны только при холодном старте приложения,
# когда VK выдаёт свежие.
LAUNCH_MAX_AGE = 60 * 60


class LaunchError(Exception):
    """Параметры запуска не приняты: нет подписи, подделана или протухла."""


def verify_vk_launch(params: Mapping[str, str]) -> str:
    """Проверить подпись параметров запуска VK и вернуть `vk_user_id`.

    На входе — плоский словарь query string (`dict(request.query_params)`).
    Бросает `LaunchError` на любую неудачу; `RuntimeError`, если ключ VK не
    задан в настройках. Значения читаются ТОЛЬКО после проверки подписи.
    """
    settings = get_settings()
    if settings.vk_secure_key is None:
        raise RuntimeError("VK_SECURE_KEY не задан — вход из VK невозможен")

    given = params.get("sign")
    if not given:
        raise LaunchError("В параметрах запуска нет подписи")

    expected = _expected_sign(params, settings.vk_secure_key.get_secret_value())
    if expected is None:
        raise LaunchError("В параметрах запуска нет ни одного vk_-параметра")

    # compare_digest, а не `==`: обычное сравнение выходит на первом
    # различающемся символе, и подпись подбирается по времени ответа.
    if not hmac.compare_digest(given, expected):
        raise LaunchError("Подпись параметров запуска неверна")

    # --- Дальше всё под защитой подписи: подменить эти значения нельзя ---

    _check_freshness(params)

    if settings.vk_app_id is not None and params.get("vk_app_id") != str(
        settings.vk_app_id
    ):
        # Подпись сошлась, значит ключ наш, а приложение — другое:
        # разъехались VK_APP_ID и VK_SECURE_KEY в настройках.
        logger.error("vk_app_id из параметров не совпал с VK_APP_ID")
        raise LaunchError("Параметры запуска выданы другому приложению")

    user_id = params.get("vk_user_id")
    if not user_id:
        raise LaunchError("В параметрах запуска нет vk_user_id")
    return user_id


def _expected_sign(params: Mapping[str, str], secret: str) -> str | None:
    """Посчитать подпись по алгоритму VK; None, если `vk_`-параметров нет.

    Алгоритм: все ключи с префиксом `vk_`, отсортированные по имени, в виде
    query string → HMAC-SHA256 на защищённом ключе → base64 url-safe без
    выравнивания (dev.vk.ru, «Подпись параметров запуска»).
    """
    vk_keys = sorted(key for key in params if key.startswith("vk_"))
    if not vk_keys:
        return None

    # urlencode кодирует пробел как '+', тогда как документация VK требует
    # %20. Расхождение оставлено намеренно: официальный пример VK на Python
    # использует именно urlencode, и подпись сходится — пробелов в vk_-полях
    # на практике не бывает. Если подпись вдруг не сойдётся на каком-то
    # параметре со значением-строкой, причина здесь.
    payload = urlencode({key: params[key] for key in vk_keys})

    digest = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    # urlsafe_b64encode сразу даёт алфавит с '-' и '_', как в sign; '=' в
    # конце VK отбрасывает (для 32 байт SHA-256 он всегда один).
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _check_freshness(params: Mapping[str, str]) -> None:
    """Не старше ли `vk_ts` допустимого. Нет параметра — пропустить с WARNING.

    Пропуск безопасен: набор `vk_`-параметров покрыт подписью целиком, то
    есть выбросить `vk_ts` из ссылки, не сломав её, нельзя. Отсутствие
    значит смену формата на стороне VK — знать об этом надо, а закрывать
    из-за неё вход всем пользователям не стоит.
    """
    raw = params.get("vk_ts")
    if raw is None:
        logger.warning("В параметрах запуска нет vk_ts — свежесть не проверена")
        return
    try:
        issued = int(raw)
    except ValueError:
        logger.warning("vk_ts не число — свежесть не проверена")
        return

    # Только «слишком старая»: подпись из будущего означает разбежавшиеся
    # часы у нас и у VK, и на пару минут вперёд это норма, а не атака.
    age = int(time.time()) - issued
    if age > LAUNCH_MAX_AGE:
        raise LaunchError(f"Параметры запуска устарели: {age} с")


def issue_session(user_id: int) -> str:
    """Выпустить сессионный JWT на `SESSION_TTL`. Без ключа — `RuntimeError`.

    В токен идёт только идентификатор пользователя: JWT подписан, но НЕ
    зашифрован — payload читает любой, кто снимет base64. Имя, дата и место
    рождения здесь появиться не должны.
    """
    settings = get_settings()
    if settings.session_secret is None:
        raise RuntimeError("SESSION_SECRET не задан — сессию выпустить нечем")

    now = int(time.time())
    payload = {
        # str: PyJWT с версии 2.10 требует строковый `sub` по RFC 7519.
        "sub": str(user_id),
        "iat": now,
        "exp": now + SESSION_TTL,
    }
    return jwt.encode(payload, settings.session_secret.get_secret_value(), _ALGORITHM)


def read_session(token: str) -> int | None:
    """Достать `user_id` из токена; None, если он протух, подделан или битый.

    Причина неудачи вызывающему не нужна: ответ во всех случаях один —
    отправить человека открыть мини-приложение заново.
    """
    settings = get_settings()
    if settings.session_secret is None:
        # Не RuntimeError: ключ могли убрать на живом сервере, и тогда 500
        # на каждый запрос хуже, чем «сессия кончилась».
        logger.error("SESSION_SECRET не задан — сессии не читаются")
        return None

    try:
        payload = jwt.decode(
            token,
            settings.session_secret.get_secret_value(),
            algorithms=[_ALGORITHM],
        )
    except jwt.ExpiredSignatureError:
        logger.debug("Сессионный токен просрочен")
        return None
    except jwt.InvalidTokenError:
        # Сюда попадает и мусор в адресной строке, и подделка — WARNING, а
        # не DEBUG: всплеск таких строк означает, что токен кто-то подбирает.
        logger.warning("Сессионный токен не прошёл проверку")
        return None

    try:
        return int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        # Токен подписан нами, но содержимое не то: остаток от прежней схемы
        # payload или ручная правка кода.
        logger.error("В сессионном токене нет пригодного sub")
        return None
