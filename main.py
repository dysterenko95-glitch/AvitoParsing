import os
import asyncio
import logging
import random
import time
from typing import Dict, List, Optional

import httpx
from bs4 import BeautifulSoup
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, LinkPreviewOptions
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# ==================== НАСТРОЙКА ЛОГИРОВАНИЯ ====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# ==================== ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
PROXY_URL = os.environ.get("PROXY_URL")  # Например: http://user:pass@host:port

if not BOT_TOKEN:
    raise ValueError("Переменная окружения BOT_TOKEN не задана!")

# ==================== ИНИЦИАЛИЗАЦИЯ БОТА ====================
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

# ==================== ХРАНИЛИЩЕ В ОЗУ (по chat_id) ====================
# Структура:
# user_data[chat_id] = {
#     "links": [url1, url2, ...],
#     "ads": {
#         url1: {
#             "ad_id_123": {"title": "...", "price": 10000, "url": "..."},
#             ...
#         },
#         ...
#     },
#     "last_check": {
#         url1: timestamp,
#         ...
#     }
# }
user_data: Dict[int, dict] = {}

# ==================== КЛАВИАТУРА ====================
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 Статус и Ссылки")],
        [KeyboardButton(text="🛑 Остановить и Сбросить все")]
    ],
    resize_keyboard=True
)

# ==================== HTTP-ЗАГОЛОВКИ ДЛЯ АВИТО ====================
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}

# ==================== ФУНКЦИЯ ЗАГРУЗКИ СТРАНИЦЫ ====================
async def fetch_page(url: str) -> Optional[str]:
    proxy = PROXY_URL if PROXY_URL else None
    try:
        async with httpx.AsyncClient(
            proxy=proxy,
            headers=HEADERS,
            timeout=30.0,
            follow_redirects=True
        ) as client:
            response = await client.get(url)
            if response.status_code == 200:
                return response.text
            else:
                logging.warning(
                    f"Статус {response.status_code} при запросе: {url}"
                )
                return None
    except Exception as exc:
        logging.error(f"Ошибка сети при запросе {url}: {exc}")
        return None

# ==================== ПАРСЕР АВИТО ====================
def parse_avito(html: str) -> Dict[str, dict]:
    """
    Парсит HTML страницы Авито.
    Возвращает словарь: {ad_id: {"title": str, "price": int|None, "url": str}}
    """
    soup = BeautifulSoup(html, "html.parser")
    ads: Dict[str, dict] = {}

    # Попытка 1: по data-marker="item" (основной способ Авито)
    items = soup.find_all("div", attrs={"data-marker": "item"})

    # Попытка 2: fallback по классу iva-item-root
    if not items:
        items = soup.find_all(
            "div",
            class_=lambda x: x and "iva-item-root-" in x
        )

    # Попытка 3: fallback по article / schema.org
    if not items:
        items = soup.find_all("article")

    for item in items:
        try:
            # --- ССЫЛКА ---
            a_tag = item.find("a", attrs={"data-marker": "item-title"})
            if not a_tag:
                a_tag = item.find("a", href=True)
            if not a_tag:
                continue

            href = a_tag.get("href", "")
            if href.startswith("/"):
                href = "https://www.avito.ru" + href

            # --- ID ОБЪЯВЛЕНИЯ ---
            ad_id = None
            if "_" in href:
                ad_id = href.split("_")[-1].split("?")[0]
            else:
                ad_id = href.strip("/").split("/")[-1]

            # --- ЗАГОЛОВОК ---
            title = a_tag.get_text(strip=True)
            if not title:
                title_tag = item.find(attrs={"data-marker": "item-title"})
                if title_tag:
                    title = title_tag.get_text(strip=True)
            if not title:
                h3 = item.find("h3")
                if h3:
                    title = h3.get_text(strip=True)

            # --- ЦЕНА ---
            price = None
            price_tag = item.find(attrs={"data-marker": "item-price"})
            if price_tag:
                price_text = price_tag.get_text(strip=True)
                digits = "".join(ch for ch in price_text if ch.isdigit())
                if digits:
                    price = int(digits)

            if not price:
                meta_price = item.find("meta", attrs={"itemprop": "price"})
                if meta_price:
                    content = meta_price.get("content")
                    if content:
                        try:
                            price = int(content)
                        except ValueError:
                            pass

            if ad_id and title:
                ads[ad_id] = {
                    "title": title,
                    "price": price,
                    "url": href
                }
        except Exception as exc:
            logging.error(f"Ошибка парсинга элемента: {exc}")
            continue

    return ads

# ==================== ОБРАБОТЧИКИ СООБЩЕНИЙ ====================

@dp.message(F.text == "/start")
async def handler_start(message: types.Message):
    chat_id = message.chat.id
    if chat_id not in user_data:
        user_data[chat_id] = {
            "links": [],
            "ads": {},
            "last_check": {}
        }

    await message.answer(
        "👋 Привет!\n\n"
        "Отправь мне ссылку с Авито (с уже настроенными фильтрами в браузере), "
        "и я начну следить за новыми объявлениями и изменениями цен.\n\n"
        "Используй кнопки ниже для управления.",
        reply_markup=main_kb
    )


@dp.message(F.text == "📊 Статус и Ссылки")
async def handler_status(message: types.Message):
    chat_id = message.chat.id
    data = user_data.get(chat_id, {"links": []})
    links = data.get("links", [])

    if not links:
        await message.answer(
            "📭 У тебя пока нет отслеживаемых ссылок.\n"
            "Отправь ссылку с Авито, чтобы добавить её в список."
        )
        return

    text = f"📊 <b>Твои ссылки ({len(links)}):</b>\n\n"
    for idx, link in enumerate(links, start=1):
        text += f"{idx}. {link}\n"

    total_ads = sum(len(v) for v in data.get("ads", {}).values())
    text += f"\n📌 Всего сохранённых объявлений: {total_ads}"

    await message.answer(text)


@dp.message(F.text == "🛑 Остановить и Сбросить все")
async def handler_reset(message: types.Message):
    chat_id = message.chat.id
    if chat_id in user_data:
        user_data[chat_id] = {
            "links": [],
            "ads": {},
            "last_check": {}
        }

    await message.answer(
        "🛑 Все ссылки и история объявлений полностью очищены.\n"
        "Бот больше ничего не отслеживает для тебя.",
        reply_markup=main_kb
    )


@dp.message(F.text.startswith("http"))
async def handler_add_link(message: types.Message):
    chat_id = message.chat.id
    url = message.text.strip()

    if "avito.ru" not in url:
        await message.answer(
            "❌ Это не похоже на ссылку с Авито.\n"
            "Отправь корректную ссылку, начинающуюся с http."
        )
        return

    if chat_id not in user_data:
        user_data[chat_id] = {
            "links": [],
            "ads": {},
            "last_check": {}
        }

    if url in user_data[chat_id]["links"]:
        await message.answer("⚠️ Эта ссылка уже есть в твоём списке отслеживания.")
        return

    # Добавляем ссылку
    user_data[chat_id]["links"].append(url)
    user_data[chat_id]["ads"][url] = {}
    user_data[chat_id]["last_check"][url] = 0

    await message.answer(
        "⏳ Загружаю текущие объявления по ссылке, подождите..."
    )

    # Первичная загрузка: запоминаем как "старые", без уведомлений
    html = await fetch_page(url)
    if html:
        ads = parse_avito(html)
        user_data[chat_id]["ads"][url] = ads
        user_data[chat_id]["last_check"][url] = time.monotonic()
        await message.answer(
            f"✅ Ссылка добавлена!\n"
            f"Найдено и сохранено {len(ads)} объявлений.\n\n"
            f"Я буду проверять её каждые 5–10 минут и сообщу, "
            f"если появится что-то новое или изменится цена."
        )
    else:
        await message.answer(
            "⚠️ Не удалось загрузить страницу прямо сейчас.\n"
            "Ссылка добавлена в список, я попробую проверить её при следующем цикле."
        )


# ==================== ФОНОВЫЙ ЧЕКЕР ====================

async def check_one_link(chat_id: int, url: str):
    """
    Проверяет одну ссылку для одного пользователя.
    Сравнивает с сохранёнными объявлениями и отправляет уведомления.
    """
    data = user_data[chat_id]
    # Обновляем время проверки сразу, чтобы при ошибке не спамить запросами
    data["last_check"][url] = time.monotonic()

    html = await fetch_page(url)
    if not html:
        return

    current_ads = parse_avito(html)
    old_ads = data["ads"].get(url, {})

    new_ads: List[dict] = []
    price_changed: List[dict] = []

    for ad_id, ad_info in current_ads.items():
        if ad_id not in old_ads:
            # Новое объявление
            new_ads.append(ad_info)
            old_ads[ad_id] = ad_info
        else:
            # Проверяем изменение цены
            old_price = old_ads[ad_id].get("price")
            new_price = ad_info.get("price")
            if (
                old_price is not None
                and new_price is not None
                and old_price != new_price
            ):
                diff = new_price - old_price
                sign = "📉" if diff < 0 else "📈"
                price_changed.append({
                    "title": ad_info["title"],
                    "url": ad_info["url"],
                    "old_price": old_price,
                    "new_price": new_price,
                    "diff": diff,
                    "sign": sign
                })
            # Обновляем данные объявления в любом случае
            old_ads[ad_id] = ad_info

    # Сохраняем обновлённый словарь
    data["ads"][url] = old_ads

    # --- УВЕДОМЛЕНИЯ: НОВЫЕ ОБЪЯВЛЕНИЯ ---
    for ad in new_ads:
        price_str = f"{ad['price']} ₽" if ad["price"] else "Цена не указана"
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    f"🆕 <b>Новое объявление!</b>\n\n"
                    f"<a href='{ad['url']}'>{ad['title']}</a>\n"
                    f"💰 {price_str}"
                ),
                link_preview_options=LinkPreviewOptions(is_disabled=True)
            )
            await asyncio.sleep(0.5)
        except Exception as exc:
            logging.error(f"Ошибка отправки сообщения: {exc}")

    # --- УВЕДОМЛЕНИЯ: ИЗМЕНЕНИЕ ЦЕНЫ ---
    for ad in price_changed:
        diff_str = f"{ad['diff']:+d} ₽"
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    f"{ad['sign']} <b>Изменение цены!</b>\n\n"
                    f"<a href='{ad['url']}'>{ad['title']}</a>\n"
                    f"💰 Старая цена: {ad['old_price']} ₽\n"
                    f"💰 Новая цена: {ad['new_price']} ₽\n"
                    f"📊 Разница: {diff_str}"
                ),
                link_preview_options=LinkPreviewOptions(is_disabled=True)
            )
            await asyncio.sleep(0.5)
        except Exception as exc:
            logging.error(f"Ошибка отправки сообщения: {exc}")


async def background_checker():
    """
    Бесконечный цикл: раз в минуту проходит по всем пользователям и ссылкам.
    Проверяет ссылку, только если с момента последней проверки прошло 5–10 минут.
    """
    while True:
        try:
            now = time.monotonic()
            tasks = []

            for chat_id, data in list(user_data.items()):
                for url in data.get("links", []):
                    last_check = data.get("last_check", {}).get(url, 0)
                    # Случайный интервал между 5 и 10 минутами (300–600 сек)
                    interval = random.randint(300, 600)
                    if now - last_check >= interval:
                        tasks.append(check_one_link(chat_id, url))

            if tasks:
                for task in tasks:
                    try:
                        await task
                        # Небольшая задержка между запросами, чтобы не забанили
                        await asyncio.sleep(random.uniform(2, 5))
                    except Exception as exc:
                        logging.error(f"Ошибка в задаче проверки: {exc}")

            # Ждём минуту перед следующей итерацией планировщика
            await asyncio.sleep(60)

        except Exception as exc:
            logging.error(f"Критическая ошибка фонового чекера: {exc}")
            await asyncio.sleep(60)


# ==================== DUMMY WEB-СЕРВЕР (для Render.com) ====================
async def run_dummy_server():
    """
    Render.com (бесплатный тариф Web Service) требует открытый порт.
    Этот сервер просто отвечает 200 OK и не мешает работе бота.
    """
    port = int(os.environ.get("PORT", 10000))

    async def handle_request(reader, writer):
        try:
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/plain\r\n"
                b"Content-Length: 11\r\n"
                b"Connection: close\r\n\r\n"
                b"Bot is OK!"
            )
            await writer.drain()
        except Exception as exc:
            logging.error(f"Ошибка dummy server: {exc}")
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle_request, "0.0.0.0", port)
    logging.info(f"Dummy web-сервер запущен на порту {port}")
    async with server:
        await server.serve_forever()


# ==================== ТОЧКА ВХОДА ====================
async def main():
    # Запускаем фоновые задачи
    asyncio.create_task(run_dummy_server())
    asyncio.create_task(background_checker())

    # Запускаем Telegram-бота (polling)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
