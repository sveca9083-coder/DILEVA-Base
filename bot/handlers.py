"""Telegram handlers for DILEVA Base."""

import logging
import os

from telegram import ReplyKeyboardMarkup, Update
from telegram.error import Conflict, NetworkError, TimedOut
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot import db


logger = logging.getLogger(__name__)

DB_KEY = "db"
REDIS_KEY = "redis"


# ============================================================
# ADMIN ACCESS
# ============================================================

def get_admin_ids() -> set[int]:
    raw = os.getenv("ADMIN_IDS", "")
    return {
        int(value.strip())
        for value in raw.split(",")
        if value.strip().isdigit()
    }


def is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id in get_admin_ids()


async def require_admin(update: Update) -> bool:
    user = update.effective_user
    message = update.effective_message

    if user is None or message is None:
        return False

    if not is_admin(user.id):
        await message.reply_text(
            "⛔ У тебя нет доступа к DILEVA Base."
        )
        return False

    return True


# ============================================================
# STATUSES
# ============================================================

STATUS_NEW = "new"
STATUS_NO_REPLY = "no_reply"
STATUS_REFUSED = "refused"
STATUS_UNDER_16 = "under_16"
STATUS_JOINED = "joined"

STATUS_NAMES = {
    STATUS_NEW: "🆕 Новые",
    STATUS_NO_REPLY: "⏳ Не отвечает",
    STATUS_REFUSED: "🚫 Отказано",
    STATUS_UNDER_16: "🔞 Нету 16",
    STATUS_JOINED: "✅ Вступил",
}


# ============================================================
# MENU
# ============================================================

MENU_NEW = "🆕 Новые"
MENU_NO_REPLY = "⏳ Не отвечает"
MENU_REFUSED = "🚫 Отказано"
MENU_UNDER_16 = "🔞 Нету 16"
MENU_JOINED = "✅ Вступил"

MENU_SEARCH = "🔎 Поиск"
MENU_IMPORT = "📥 Импорт"
MENU_STATS = "📊 Статистика"


MAIN_MENU = ReplyKeyboardMarkup(
    [
        [MENU_NEW, MENU_NO_REPLY],
        [MENU_REFUSED, MENU_UNDER_16],
        [MENU_JOINED],
        [MENU_SEARCH, MENU_IMPORT],
        [MENU_STATS],
    ],
    resize_keyboard=True,
    is_persistent=True,
    input_field_placeholder="Выбери раздел",
)


BOT_COMMANDS = (
    ("start", "Открыть DILEVA Base"),
)


# ============================================================
# START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if not await require_admin(update):
        return

    message = update.effective_message
    user = update.effective_user

    if message is None or user is None:
        return

    pool = context.bot_data.get(DB_KEY)

    if pool is not None:
        await db.upsert_user(
            pool,
            user.id,
            user.username,
            user.first_name,
        )

    await message.reply_text(
        "👋 Добро пожаловать в DILEVA Base!\n\n"
        "Выбери нужный раздел:",
        reply_markup=MAIN_MENU,
    )


# ============================================================
# STATUS LIST
# ============================================================

async def show_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    status: str,
) -> None:

    if not await require_admin(update):
        return

    message = update.effective_message
    pool = context.bot_data.get(DB_KEY)

    if message is None:
        return

    if pool is None:
        await message.reply_text(
            "⚠️ База данных сейчас недоступна."
        )
        return
contacts = await db.get_contacts_by_status(
    pool,
    status,
    limit=50,
)
    
    title = STATUS_NAMES[status]

    if not contacts:
        await message.reply_text(
            f"{title}\n\nПока здесь никого нет."
        )
        return

    lines = [title, ""]

    for contact in contacts:
       username = contact["username"]

name = f"@{username}"

if contact["claimed_by"]:
    name += f"\n   🔒 Занят админом ID {contact['claimed_by']}" 
        lines.append(name)

    await message.reply_text(
        "\n".join(lines)
    )


# ============================================================
# SEARCH
# ============================================================

async def search_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if not await require_admin(update):
        return

    context.user_data["mode"] = "search"

    await update.effective_message.reply_text(
        "🔎 Введи username для поиска.\n\n"
        "Можно с @ или без него."
    )


async def search_user(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if not await require_admin(update):
        return

    if context.user_data.get("mode") != "search":
        return

    message = update.effective_message

    if message is None or not message.text:
        return

    context.user_data.pop("mode", None)

    username = message.text.strip().lstrip("@")

    pool = context.bot_data.get(DB_KEY)

    if pool is None:
        await message.reply_text(
            "⚠️ База данных сейчас недоступна."
        )
        return

     contact = await db.get_contact_by_username(
    pool,
    username,
)

    if contact is None:
        await message.reply_text(
            f"❌ @{username} не найден."
        )
        return

    status = STATUS_NAMES.get(
    contact["status"],
    contact["status"],
)

text = (
    "🔎 Контакт найден\n\n"
    f"👤 @{contact['username']}\n"
    f"📌 {status}\n"
)

if contact["telegram_id"]:
    text += f"🆔 {contact['telegram_id']}\n"

if contact["source"]:
    text += f"📍 Источник: {contact['source']}\n"

if contact["notes"]:
    text += f"📝 Заметка: {contact['notes']}\n"

if contact["claimed_by"]:
    text += (
        f"🔒 Закреплён за админом "
        f"{contact['claimed_by']}\n"
    )

         

    await message.reply_text(text)

async def text_router(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Route normal text messages according to the current mode."""

    mode = context.user_data.get("mode")

    if mode == "search":
        await search_user(update, context)
        return

    if mode == "import":
        await import_users(update, context)
        return
# ============================================================
# IMPORT
# ============================================================

async def import_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if not await require_admin(update):
        return

    context.user_data["mode"] = "import"

    await update.effective_message.reply_text(
        "📥 Импорт\n\n"
        "Отправь usernames одним сообщением.\n"
        "Каждый username — с новой строки.\n\n"
        "Например:\n"
        "@username1\n"
        "@username2\n"
        "username3"
    )

async def import_users(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if not await require_admin(update):
        return

    if context.user_data.get("mode") != "import":
        return

    message = update.effective_message

    if message is None or not message.text:
        return

    context.user_data.pop("mode", None)

    pool = context.bot_data.get(DB_KEY)

    if pool is None:
        await message.reply_text(
            "⚠️ База данных сейчас недоступна."
        )
        return

    usernames = []

    for line in message.text.splitlines():
        username = line.strip().lstrip("@").strip()

        if username:
            usernames.append(username)

    usernames = list(dict.fromkeys(
        username.lower()
        for username in usernames
    ))

    if not usernames:
        await message.reply_text(
            "❌ Не найдено ни одного username."
        )
        return

    added = 0
    skipped = 0

    for username in usernames:

        existing = await db.get_contact_by_username(
            pool,
            username,
        )

        if existing:
            skipped += 1
            continue

        await db.add_contact(
            pool,
            username,
            added_by=update.effective_user.id,
            source="import",
        )

        added += 1

    await message.reply_text(
        "📥 Импорт завершён.\n\n"
        f"✅ Добавлено: {added}\n"
        f"⏭ Уже были в базе: {skipped}\n"
        f"📊 Всего обработано: {len(usernames)}"
    )

# ============================================================
# STATISTICS
# ============================================================

async def statistics(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if not await require_admin(update):
        return

    message = update.effective_message
    pool = context.bot_data.get(DB_KEY)

    if message is None:
        return

    if pool is None:
        await message.reply_text(
            "⚠️ База данных сейчас недоступна."
        )
        return

    total = await db.count_contacts(pool)
    new = await db.count_by_status(pool, STATUS_NEW)
    no_reply = await db.count_by_status(pool, STATUS_NO_REPLY)
    refused = await db.count_by_status(pool, STATUS_REFUSED)
    under_16 = await db.count_by_status(pool, STATUS_UNDER_16)
    joined = await db.count_by_status(pool, STATUS_JOINED)

    await message.reply_text(
        "📊 Статистика DILEVA Base\n\n"
        f"👥 Всего: {total}\n\n"
        f"🆕 Новые: {new}\n"
        f"⏳ Не отвечает: {no_reply}\n"
        f"🚫 Отказано: {refused}\n"
        f"🔞 Нету 16: {under_16}\n"
        f"✅ Вступил: {joined}"
    )


# ============================================================
# MENU
# ============================================================

async def menu_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if not await require_admin(update):
        return

    message = update.effective_message

    if message is None or not message.text:
        return

    text = message.text.strip()

    if text == MENU_NEW:
        await show_status(update, context, STATUS_NEW)

    elif text == MENU_NO_REPLY:
        await show_status(update, context, STATUS_NO_REPLY)

    elif text == MENU_REFUSED:
        await show_status(update, context, STATUS_REFUSED)

    elif text == MENU_UNDER_16:
        await show_status(update, context, STATUS_UNDER_16)

    elif text == MENU_JOINED:
        await show_status(update, context, STATUS_JOINED)

    elif text == MENU_SEARCH:
        await search_start(update, context)

    elif text == MENU_IMPORT:
        await import_start(update, context)

    elif text == MENU_STATS:
        await statistics(update, context)


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    error = context.error

    if isinstance(
        error,
        (Conflict, NetworkError, TimedOut),
    ):
        logger.warning(
            "Transient Telegram error: %s",
            error,
        )
        return

    logger.exception(
        "Error while processing update: %s",
        update,
        exc_info=error,
    )


# ============================================================
# COMMANDS
# ============================================================

async def set_bot_commands(
    application: Application,
) -> None:

    await application.bot.set_my_commands(
        BOT_COMMANDS
    )


# ============================================================
# REGISTRATION
# ============================================================

def register_handlers(
    application: Application,
) -> None:

    application.add_handler(
        CommandHandler("start", start)
    )

    menu_pattern = (
        "^("
        + "|".join(
            [
                MENU_NEW,
                MENU_NO_REPLY,
                MENU_REFUSED,
                MENU_UNDER_16,
                MENU_JOINED,
                MENU_SEARCH,
                MENU_IMPORT,
                MENU_STATS,
            ]
        )
        + ")$"
    )

    application.add_handler(
        MessageHandler(
            filters.Regex(menu_pattern),
            menu_button,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_router,
        )
    )
