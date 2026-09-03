"""Telegram handlers for DILEVA Base."""

import logging
import os
from datetime import datetime, timezone

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


# ============================================================
# ADMIN ACCESS
# ============================================================

def get_admin_ids() -> set[int]:
    """Read administrator Telegram IDs from ADMIN_IDS."""
    raw = os.getenv("ADMIN_IDS", "")

    result = set()

    for value in raw.split(","):
        value = value.strip()

        if value.isdigit():
            result.add(int(value))

    return result


def is_admin(user_id: int | None) -> bool:
    """Check whether a Telegram user is an administrator."""
    if user_id is None:
        return False

    return user_id in get_admin_ids()


async def require_admin(
    update: Update,
) -> bool:
    """Allow only configured administrators to use the bot."""

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


# ============================================================
# BOT COMMANDS
# ============================================================

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
        "Здесь можно вести общую базу людей и распределять их между администраторами.",
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

    if message is None:
        return

    pool = context.bot_data.get(DB_KEY)

    if pool is None:
        await message.reply_text(
            "⚠️ База данных сейчас недоступна."
        )
        return

    users = await db.get_users_by_status(
        pool,
        status,
        limit=50,
    )

    title = STATUS_NAMES[status]

    if not users:
        await message.reply_text(
            f"{title}\n\n"
            "Пока здесь никого нет."
        )
        return

    lines = [f"{title}\n"]

    for user in users:
        username = user["username"]

        if username:
            name = f"@{username}"
        else:
            name = user["first_name"] or str(user["telegram_id"])

        claimed_by = user["claimed_by"]

        if claimed_by:
            name += f" 🔒 ID админа: {claimed_by}"

        lines.append(name)

    await message.reply_text(
        "\n".join(lines)
    )


async def new_users(update, context):
    await show_status(update, context, STATUS_NEW)


async def no_reply_users(update, context):
    await show_status(update, context, STATUS_NO_REPLY)


async def refused_users(update, context):
    await show_status(update, context, STATUS_REFUSED)


async def under_16_users(update, context):
    await show_status(update, context, STATUS_UNDER_16)


async def joined_users(update, context):
    await show_status(update, context, STATUS_JOINED)


# ============================================================
# SEARCH
# ============================================================

async def search_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if not await require_admin(update):
        return

    context.user_data["waiting_for_search"] = True

    message = update.effective_message

    if message:
        await message.reply_text(
            "🔎 Введи username для поиска.\n\n"
            "Можно с @ или без него."
        )


async def search_user(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if not await require_admin(update):
        return

    message = update.effective_message

    if message is None or not message.text:
        return

    if not context.user_data.get("waiting_for_search"):
        return

    context.user_data["waiting_for_search"] = False

    username = message.text.strip()

    pool = context.bot_data.get(DB_KEY)

    if pool is None:
        await message.reply_text(
            "⚠️ База данных сейчас недоступна."
        )
        return

    user = await db.get_user_by_username(
        pool,
        username,
    )

    if user is None:
        await message.reply_text(
            f"❌ {username} не найден в базе."
        )
        return

    status = STATUS_NAMES.get(
        user["status"],
        user["status"],
    )

    actual_username = user["username"] or "без username"

    text = (
        "🔎 Найден пользователь\n\n"
        f"👤 @{actual_username}\n"
        f"🆔 {user['telegram_id']}\n"
        f"📌 Статус: {status}\n"
    )

    if user["source"]:
        text += f"📍 Источник: {user['source']}\n"

    if user["notes"]:
        text += f"📝 Заметка: {user['notes']}\n"

    if user["claimed_by"]:
        text += (
            f"🔒 Закреплён за админом: "
            f"{user['claimed_by']}\n"
        )

    await message.reply_text(text)


# ============================================================
# IMPORT
# ============================================================

async def import_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if not await require_admin(update):
        return

    context.user_data["waiting_for_import"] = True

    message = update.effective_message

    if message:
        await message.reply_text(
            "📥 Импорт пользователей\n\n"
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

    message = update.effective_message

    if message is None or not message.text:
        return

    if not context.user_data.get("waiting_for_import"):
        return

    context.user_data["waiting_for_import"] = False

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

    usernames = list(dict.fromkeys(usernames))

    if not usernames:
        await message.reply_text(
            "❌ Не нашла ни одного username."
        )
        return

    added = 0
    skipped = 0

    for username in usernames:
        existing = await db.get_user_by_username(
            pool,
            username,
        )

        if existing:
            skipped += 1
            continue

        # Telegram ID неизвестен, поэтому для импорта
        # создаём временный отрицательный ID.
        fake_id = -abs(hash(username))

        try:
            await pool.execute(
                """
                INSERT INTO users (
                    telegram_id,
                    username,
                    status,
                    added_by
                )
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (telegram_id) DO NOTHING;
                """,
                fake_id,
                username,
                STATUS_NEW,
                update.effective_user.id,
            )

            added += 1

        except Exception:
            logger.exception(
                "Failed to import @%s",
                username,
            )

    await message.reply_text(
        "📥 Импорт завершён.\n\n"
        f"✅ Добавлено: {added}\n"
        f"⏭ Пропущено: {skipped}"
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

    if message is None:
        return

    pool = context.bot_data.get(DB_KEY)

    if pool is None:
        await message.reply_text(
            "⚠️ База данных сейчас недоступна."
        )
        return

    total = await db.count_users(pool)

    new = await db.count_by_status(
        pool,
        STATUS_NEW,
    )

    no_reply = await db.count_by_status(
        pool,
        STATUS_NO_REPLY,
    )

    refused = await db.count_by_status(
        pool,
        STATUS_REFUSED,
    )

    under_16 = await db.count_by_status(
        pool,
        STATUS_UNDER_16,
    )

    joined = await db.count_by_status(
        pool,
        STATUS_JOINED,
    )

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
# MENU ROUTER
# ============================================================

async def menu_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    message = update.effective_message

    if message is None or not message.text:
        return

    text = message.text.strip()

    if text == MENU_NEW:
        await new_users(update, context)

    elif text == MENU_NO_REPLY:
        await no_reply_users(update, context)

    elif text == MENU_REFUSED:
        await refused_users(update, context)

    elif text == MENU_UNDER_16:
        await under_16_users(update, context)

    elif text == MENU_JOINED:
        await joined_users(update, context)

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
# TELEGRAM COMMANDS
# ============================================================

async def set_bot_commands(
    application: Application,
) -> None:

    await application.bot.set_my_commands(
        BOT_COMMANDS
    )


# ============================================================
# HANDLER REGISTRATION
# ============================================================

def register_handlers(
    application: Application,
) -> None:

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        MessageHandler(
            filters.Regex(
                f"^({'|'.join(map(lambda x: x.replace(' ', r'\\s+'), [
                    MENU_NEW,
                    MENU_NO_REPLY,
                    MENU_REFUSED,
                    MENU_UNDER_16,
                    MENU_JOINED,
                    MENU_SEARCH,
                    MENU_IMPORT,
                    MENU_STATS,
                ]))})$"
            ),
            menu_button,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            search_user,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,

            import_users,

        )

    )
