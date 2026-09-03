"""Telegram handlers for DILEVA Base."""

import logging
import os

from telegram import Update, ReplyKeyboardMarkup
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


# =========================
# STATUSES
# =========================

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


# =========================
# MENU
# =========================

MENU_NEW = "🆕 Новые"
MENU_NO_REPLY = "⏳ Не отвечает"
MENU_REFUSED = "🚫 Отказано"
MENU_UNDER_16 = "🔞 Нету 16"
MENU_JOINED = "✅ Вступил"

MENU_SEARCH = "🔎 Поиск"
MENU_IMPORT = "📥 Импорт"
MENU_STATS = "📊 Статистика"


def get_menu_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [MENU_NEW, MENU_NO_REPLY],
        [MENU_REFUSED, MENU_UNDER_16],
        [MENU_JOINED],
        [MENU_SEARCH, MENU_IMPORT],
        [MENU_STATS],
    ]

    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
    )


# =========================
# ADMIN
# =========================

def get_admin_ids() -> set[int]:
    raw = os.getenv("ADMIN_IDS", "")

    admin_ids = set()

    for value in raw.split(","):
        value = value.strip()

        if not value:
            continue

        try:
            admin_ids.add(int(value))
        except ValueError:
            logger.warning(
                "Invalid ADMIN_IDS value: %s",
                value,
            )

    return admin_ids


async def require_admin(update: Update) -> bool:
    user = update.effective_user
    message = update.effective_message

    if user is None:
        return False

    if user.id not in get_admin_ids():
        if message is not None:
            await message.reply_text(
                "⛔ У тебя нет доступа к DILEVA Base."
            )

        return False

    return True


# =========================
# START
# =========================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if not await require_admin(update):
        return

    user = update.effective_user
    message = update.effective_message

    if user is None or message is None:
        return

    pool = context.bot_data.get(DB_KEY)

    if pool is not None:
        try:
            await db.upsert_user(
                pool,
                telegram_id=user.id,
                username=user.username,
                first_name=user.first_name,
            )
        except Exception:
            logger.exception(
                "Failed to save Telegram user."
            )

    context.user_data.pop("mode", None)

    await message.reply_text(
        "👋 Добро пожаловать в DILEVA Base.\n\n"
        "Выбирай раздел:",
        reply_markup=get_menu_keyboard(),
    )


# =========================
# MENU BUTTON
# =========================

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
        await show_status(
            update,
            context,
            STATUS_NEW,
        )

    elif text == MENU_NO_REPLY:
        await show_status(
            update,
            context,
            STATUS_NO_REPLY,
        )

    elif text == MENU_REFUSED:
        await show_status(
            update,
            context,
            STATUS_REFUSED,
        )

    elif text == MENU_UNDER_16:
        await show_status(
            update,
            context,
            STATUS_UNDER_16,
        )

    elif text == MENU_JOINED:
        await show_status(
            update,
            context,
            STATUS_JOINED,
        )

    elif text == MENU_SEARCH:
        await search_start(
            update,
            context,
        )

    elif text == MENU_IMPORT:
        await import_start(
            update,
            context,
        )

    elif text == MENU_STATS:
        await statistics(
            update,
            context,
        )


# =========================
# STATUS LIST
# =========================

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

    try:
        contacts = await db.get_contacts_by_status(
            pool,
            status,
            limit=50,
        )
    except Exception:
        logger.exception(
            "Failed to load contacts."
        )

        await message.reply_text(
            "❌ Не удалось загрузить список."
        )
        return

    title = STATUS_NAMES.get(
        status,
        status,
    )

    if not contacts:
        await message.reply_text(
            f"{title}\n\n"
            "Пока здесь никого нет."
        )
        return

    lines = [
        title,
        "",
    ]

    for contact in contacts:
        username = contact["username"]

        line = f"@{username}"

        if contact["claimed_by"]:
            line += (
                "\n   🔒 Занят админом ID "
                f"{contact['claimed_by']}"
            )

        lines.append(line)

    await message.reply_text(
        "\n".join(lines)
    )


# =========================
# SEARCH
# =========================

async def search_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if not await require_admin(update):
        return

    message = update.effective_message

    if message is None:
        return

    context.user_data["mode"] = "search"

    await message.reply_text(
        "🔎 Введи username для поиска.\n\n"
        "Например:\n"
        "@username"
    )


async def search_user(
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

        context.user_data.pop("mode", None)
        return

    username = message.text.strip()

    if not username:
        await message.reply_text(
            "❌ Введи username."
        )
        return

    try:
        contact = await db.get_contact_by_username(
            pool,
            username,
        )
    except Exception:
        logger.exception(
            "Search failed."
        )

        await message.reply_text(
            "❌ Ошибка поиска."
        )
        return

    context.user_data.pop("mode", None)

    if contact is None:
        await message.reply_text(
            f"🔎 @{username.lstrip('@')}\n\n"
            "❌ Пользователь не найден."
        )
        return

    status_name = STATUS_NAMES.get(
        contact["status"],
        contact["status"],
    )

    lines = [
        "🔎 Найден пользователь",
        "",
        f"👤 @{contact['username']}",
        f"📌 Статус: {status_name}",
    ]

    if contact["first_name"]:
        lines.append(
            f"📝 Имя: {contact['first_name']}"
        )

    if contact["claimed_by"]:
        lines.append(
            "🔒 Занят админом ID "
            f"{contact['claimed_by']}"
        )

    if contact["notes"]:
        lines.append(
            f"💬 Заметка: {contact['notes']}"
        )

    await message.reply_text(
        "\n".join(lines)
    )


# =========================
# IMPORT
# =========================

async def import_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if not await require_admin(update):
        return

    message = update.effective_message

    if message is None:
        return

    context.user_data["mode"] = "import"

    await message.reply_text(
        "📥 Отправь список username.\n\n"
        "По одному в строке:\n"
        "@user1\n"
        "@user2\n"
        "@user3"
    )


async def import_users(
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

        context.user_data.pop("mode", None)
        return

    raw_text = message.text or ""

    usernames = []

    for line in raw_text.splitlines():
        username = line.strip()

        if not username:
            continue

        username = username.lstrip("@").strip()

        if username:
            usernames.append(username)

    if not usernames:
        await message.reply_text(
            "❌ Не нашла ни одного username."
        )
        return

    unique_usernames = []
    seen = set()

    for username in usernames:
        normalized = username.lower()

        if normalized in seen:
            continue

        seen.add(normalized)
        unique_usernames.append(username)

    added = 0
    skipped = 0

    for username in unique_usernames:
        try:
            existing = await db.get_contact_by_username(
                pool,
                username,
            )

            if existing is not None:
                skipped += 1
                continue

            contact = await db.add_contact(
                pool,
                username=username,
                added_by=update.effective_user.id,
                source="import",
            )

            if contact is not None:
                added += 1
            else:
                skipped += 1

        except Exception:
            logger.exception(
                "Failed to import @%s",
                username,
            )
            skipped += 1

    context.user_data.pop("mode", None)

    await message.reply_text(
        "📥 Импорт завершён.\n\n"
        f"✅ Добавлено: {added}\n"
        f"⏭ Пропущено: {skipped}"
    )


# =========================
# STATISTICS
# =========================

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

    try:
        total = await db.count_contacts(pool)

        new_count = await db.count_by_status(
            pool,
            STATUS_NEW,
        )

        no_reply_count = await db.count_by_status(
            pool,
            STATUS_NO_REPLY,
        )

        refused_count = await db.count_by_status(
            pool,
            STATUS_REFUSED,
        )

        under_16_count = await db.count_by_status(
            pool,
            STATUS_UNDER_16,
        )

        joined_count = await db.count_by_status(
            pool,
            STATUS_JOINED,
        )

    except Exception:
        logger.exception(
            "Failed to get statistics."
        )

        await message.reply_text(
            "❌ Не удалось получить статистику."
        )
        return

    await message.reply_text(
        "📊 Статистика DILEVA Base\n\n"
        f"👥 Всего: {total}\n\n"
        f"🆕 Новые: {new_count}\n"
        f"⏳ Не отвечает: {no_reply_count}\n"
        f"🚫 Отказано: {refused_count}\n"
        f"🔞 Нету 16: {under_16_count}\n"
        f"✅ Вступил: {joined_count}"
    )


# =========================
# TEXT ROUTER
# =========================

async def text_router(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if not await require_admin(update):
        return

    mode = context.user_data.get("mode")

    if mode == "search":
        await search_user(
            update,
            context,
        )
        return

    if mode == "import":
        await import_users(
            update,
            context,
        )
        return


# =========================
# ERROR HANDLER
# =========================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    logger.error(
        "Unhandled exception:",
        exc_info=context.error,
    )


# =========================
# BOT COMMANDS
# =========================

async def set_bot_commands(
    application: Application,
) -> None:

    await application.bot.set_my_commands(
        [
            (
                "start",
                "Запустить DILEVA Base",
            ),
        ]
    )


# =========================
# REGISTER
# =========================

def register_handlers(
    application: Application,
) -> None:

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.Regex(
                r"^(🆕 Новые|⏳ Не отвечает|🚫 Отказано|🔞 Нету 16|✅ Вступил|🔎 Поиск|📥 Импорт|📊 Статистика)$"
            ),
            menu_button,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_router,
        )
    )
