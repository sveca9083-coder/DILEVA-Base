"""Telegram handlers for DILEVA Base."""

import logging
import os

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot import db

logger = logging.getLogger(__name__)

DB_KEY = "db"
REDIS_KEY = "redis"

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

MENU_NEW = "🆕 Новые"
MENU_NO_REPLY = "⏳ Не отвечает"
MENU_REFUSED = "🚫 Отказано"
MENU_UNDER_16 = "🔞 Нету 16"
MENU_JOINED = "✅ Вступил"

MENU_SEARCH = "🔎 Поиск"
MENU_IMPORT = "📥 Импорт"
MENU_STATS = "📊 Статистика"


# =========================
# KEYBOARDS
# =========================

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


def contact_keyboard(contact_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔒 Взять в работу",
                    callback_data=f"claim:{contact_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    "⏳ Не отвечает",
                    callback_data=f"no_reply:{contact_id}",
                ),
                InlineKeyboardButton(
                    "🚫 Отказ",
                    callback_data=f"refused:{contact_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🔞 Нету 16",
                    callback_data=f"under16:{contact_id}",
                ),
                InlineKeyboardButton(
                    "✅ Вступил",
                    callback_data=f"joined:{contact_id}",
                ),
            ],
        ]
    )


# =========================
# ADMIN
# =========================

def get_admin_ids() -> set[int]:
    raw = os.getenv("ADMIN_IDS", "")

    result = set()

    for value in raw.split(","):
        value = value.strip()

        if not value:
            continue

        try:
            result.add(int(value))
        except ValueError:
            logger.warning(
                "Invalid ADMIN_IDS value: %s",
                value,
            )

    return result


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
                user.id,
                user.username,
                user.first_name,
            )
        except Exception:
            logger.exception(
                "Failed to save admin."
            )

    context.user_data.clear()

    await message.reply_text(
        "👋 Добро пожаловать в DILEVA Base.\n\n"
        "Выбирай раздел:",
        reply_markup=get_menu_keyboard(),
    )


# =========================
# AUTOMATIC CHAT JOIN
# =========================

async def chat_member_update(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Detect when a user joins a chat.

    If the user already exists in contacts:
        save Telegram ID and move to joined.

    If the user is not in contacts:
        create a new contact automatically
        and set joined.
    """

    chat_member = update.chat_member

    if chat_member is None:
        return

    old_status = chat_member.old_chat_member.status
    new_status = chat_member.new_chat_member.status

    # We only care about an actual transition into membership.
    joined_statuses = {
        "member",
        "administrator",
        "creator",
    }

    if new_status not in joined_statuses:
        return

    if old_status in joined_statuses:
        return

    user = chat_member.new_chat_member.user

    if user is None or user.is_bot:
        return

    pool = context.bot_data.get(DB_KEY)

    if pool is None:
        logger.error(
            "Chat join detected but database is unavailable."
        )
        return

    try:
        contact = await db.get_contact_by_telegram_id(
            pool,
            user.id,
        )

        if contact is None and user.username:
            contact = await db.get_contact_by_username(
                pool,
                user.username,
            )

        if contact is None:
            if not user.username:
                logger.info(
                    "User %s joined without username; "
                    "cannot create username-based contact.",
                    user.id,
                )
                return

            contact = await db.add_contact(
                pool,
                username=user.username,
                source="chat_join",
            )

        if contact is None:
            logger.error(
                "Failed to create/find contact for user %s.",
                user.id,
            )
            return

        await db.update_telegram_user(
            pool,
            contact["id"],
            telegram_id=user.id,
            username=user.username,
            first_name=user.first_name,
        )

        await db.update_status(
            pool,
            contact["id"],
            STATUS_JOINED,
            admin_id=None,
            note="Automatic chat join detection",
        )

        await db.release_contact(
            pool,
            contact["id"],
            contact["claimed_by"],
        ) if contact["claimed_by"] else None

        logger.info(
            "User @%s (%s) automatically moved to joined.",
            user.username,
            user.id,
        )

    except Exception:
        logger.exception(
            "Failed to process chat join for user %s.",
            user.id,
        )


# =========================
# MENU
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

    await message.reply_text(
        f"{title}\n\n"
        "Контакты:"
    )

    for contact in contacts:
        text = f"👤 @{contact['username']}"

        if contact["age"] is not None:
            text += (
                f"\n🎂 Возраст: "
                f"{contact['age']}"
            )

        if contact["claimed_by"]:
            text += (
                "\n🔒 Занят админом ID "
                f"{contact['claimed_by']}"
            )

        await message.reply_text(
            text,
            reply_markup=contact_keyboard(
                contact["id"]
            ),
        )


# =========================
# CONTACT CALLBACKS
# =========================

async def contact_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    query = update.callback_query

    if query is None:
        return

    user = query.from_user

    if user.id not in get_admin_ids():
        await query.answer(
            "⛔ Нет доступа.",
            show_alert=True,
        )
        return

    await query.answer()

    pool = context.bot_data.get(DB_KEY)

    if pool is None:
        await query.answer(
            "⚠️ База недоступна.",
            show_alert=True,
        )
        return

    data = query.data or ""

    try:
        action, contact_id_raw = data.split(
            ":",
            1,
        )
        contact_id = int(contact_id_raw)

    except (ValueError, AttributeError):
        await query.answer(
            "❌ Некорректная команда.",
            show_alert=True,
        )
        return

    contact = await db.get_contact(
        pool,
        contact_id,
    )

    if contact is None:
        await query.answer(
            "❌ Контакт не найден.",
            show_alert=True,
        )
        return

    if action == "claim":

        if contact["claimed_by"] not in (
            None,
            user.id,
        ):
            await query.answer(
                "🔒 Этот человек уже обрабатывается другим админом.",
                show_alert=True,
            )
            return

        success = await db.claim_contact(
            pool,
            contact_id,
            user.id,
        )

        if success:
            await query.answer(
                "🔒 Контакт закреплён за тобой."
            )

        else:
            await query.answer(
                "🔒 Его уже забрал другой админ.",
                show_alert=True,
            )

        return

    if contact["claimed_by"] not in (
        None,
        user.id,
    ):
        await query.answer(
            "🔒 Этот контакт обрабатывает другой админ.",
            show_alert=True,
        )
        return

    if action == "no_reply":

        await db.set_no_reply(
            pool,
            contact_id,
            user.id,
        )

        await query.edit_message_text(
            f"👤 @{contact['username']}\n\n"
            "📌 Статус: ⏳ Не отвечает\n"
            "⏰ Таймер 48 часов запущен."
        )

        return

    if action == "refused":

        await db.update_status(
            pool,
            contact_id,
            STATUS_REFUSED,
            user.id,
        )

        await db.release_contact(
            pool,
            contact_id,
            user.id,
        )

        await query.edit_message_text(
            f"👤 @{contact['username']}\n\n"
            "📌 Статус: 🚫 Отказано"
        )

        return

    if action == "under16":

        context.user_data["waiting_age"] = contact_id

        await query.message.reply_text(
            f"🔞 Введи возраст для "
            f"@{contact['username']}.\n\n"
            "Например: 15"
        )

        return

    if action == "joined":

        await db.update_status(
            pool,
            contact_id,
            STATUS_JOINED,
            user.id,
        )

        await db.release_contact(
            pool,
            contact_id,
            user.id,
        )

        await query.edit_message_text(
            f"👤 @{contact['username']}\n\n"
            "📌 Статус: ✅ Вступил"
        )


# =========================
# AGE INPUT
# =========================

async def handle_age_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> bool:

    message = update.effective_message
    pool = context.bot_data.get(DB_KEY)

    if message is None or pool is None:
        return False

    contact_id = context.user_data.get(
        "waiting_age"
    )

    if contact_id is None:
        return False

    text = message.text.strip()

    if not text.isdigit():
        await message.reply_text(
            "❌ Введи возраст только цифрами.\n"
            "Например: 15"
        )
        return True

    age = int(text)

    if age < 1 or age > 120:
        await message.reply_text(
            "❌ Некорректный возраст."
        )
        return True

    contact = await db.get_contact(
        pool,
        contact_id,
    )

    if contact is None:
        context.user_data.pop(
            "waiting_age",
            None,
        )

        await message.reply_text(
            "❌ Контакт не найден."
        )
        return True

    if age < 16:
        await db.set_age_and_status(
            pool,
            contact_id,
            age,
            STATUS_UNDER_16,
            update.effective_user.id,
        )

        result = "🔞 Нету 16"

    else:
        await db.set_age_and_status(
            pool,
            contact_id,
            age,
            STATUS_NO_REPLY,
            update.effective_user.id,
        )

        await db.set_no_reply(
            pool,
            contact_id,
            update.effective_user.id,
        )

        result = (
            "⏳ Не отвечает\n"
            "⏰ Таймер 48 часов запущен."
        )

    context.user_data.pop(
        "waiting_age",
        None,
    )

    await message.reply_text(
        f"👤 @{contact['username']}\n\n"
        f"🎂 Возраст: {age}\n"
        f"📌 Статус: {result}"
    )

    return True


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
        "🔎 Введи username.\n\n"
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

    if message is None or pool is None:
        return

    username = message.text.strip()

    contact = await db.get_contact_by_username(
        pool,
        username,
    )

    context.user_data.pop(
        "mode",
        None,
    )

    if contact is None:
        await message.reply_text(
            f"🔎 @{username.lstrip('@')}\n\n"
            "❌ Пользователь не найден."
        )
        return

    text = (
        "🔎 Найден пользователь\n\n"
        f"👤 @{contact['username']}\n"
        f"📌 Статус: "
        f"{STATUS_NAMES.get(contact['status'], contact['status'])}"
    )

    if contact["age"] is not None:
        text += (
            f"\n🎂 Возраст: {contact['age']}"
        )

    if contact["claimed_by"]:
        text += (
            "\n🔒 Админ ID: "
            f"{contact['claimed_by']}"
        )

    if contact["notes"]:
        text += (
            f"\n📝 {contact['notes']}"
        )

    await message.reply_text(
        text,
        reply_markup=contact_keyboard(
            contact["id"]
        ),
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
        "📥 Отправь username по одному в строке:\n\n"
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

    if message is None or pool is None:
        return

    usernames = []

    for line in message.text.splitlines():
        username = line.strip().lstrip("@")

        if username:
            usernames.append(username)

    unique = []
    seen = set()

    for username in usernames:
        normalized = username.lower()

        if normalized in seen:
            continue

        seen.add(normalized)
        unique.append(username)

    added = 0
    skipped = 0

    for username in unique:
        existing = await db.get_contact_by_username(
            pool,
            username,
        )

        if existing is not None:
            skipped += 1
            continue

        contact = await db.add_contact(
            pool,
            username,
            added_by=update.effective_user.id,
            source="import",
        )

        if contact is not None:
            added += 1
        else:
            skipped += 1

    context.user_data.pop(
        "mode",
        None,
    )

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

    if message is None or pool is None:
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
            "Statistics error."
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

    if await handle_age_input(
        update,
        context,
    ):
        return

    mode = context.user_data.get(
        "mode"
    )

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
# AUTOMATIC 48 HOURS
# =========================

async def expiration_job(
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    pool = context.bot_data.get(DB_KEY)

    if pool is None:
        return

    try:
        count = await db.return_expired_no_reply(
            pool
        )

        if count:
            logger.info(
                "Returned %s contacts to new queue.",
                count,
            )

    except Exception:
        logger.exception(
            "Expiration job failed."
        )


# =========================
# ERROR
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
# COMMANDS
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

    # Automatic detection of users joining chats.
    application.add_handler(
        ChatMemberHandler(
            chat_member_update,
            ChatMemberHandler.CHAT_MEMBER,
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            contact_callback,
            pattern=r"^(claim|no_reply|refused|under16|joined):\d+$",
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

    if application.job_queue is not None:
        application.job_queue.run_repeating(
            expiration_job,
            interval=300,
            first=10,
        )
