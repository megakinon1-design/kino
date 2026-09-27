import os
import asyncio
import secrets
import time
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.requests import Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ConversationHandler, CallbackContext, CallbackQueryHandler
)
from dotenv import load_dotenv

from config import BOT_TOKEN, ADMIN_ID
from database import (
    init_db, add_video, get_video, delete_video, list_all_videos,
    register_user_start, get_total_users, get_today_users,
    get_week_users, get_active_users_last_24h,
    get_all_user_ids, create_referral, check_referral_code, get_all_referrals,
    set_ad, get_ad, remove_ad, increment_ad_count,
    get_active_mandatory_subs, is_user_completed_sub, mark_user_completed_sub,
    add_mandatory_subscription, remove_mandatory_subscription, list_mandatory_subscriptions,
    set_user_completed_sub, get_user_referral_count, update_last_activity
)

load_dotenv()

# ======================== Holatlar ========================
WAITING_FOR_VIDEO, WAITING_FOR_CUSTOM_CODE, WAITING_FOR_DESCRIPTION = range(3)
WAITING_BROADCAST = 3
WAITING_REF_NAME = 4
WAITING_AD_CONTENT = 5

# ======================== Webhook ========================
WEBHOOK_PATH = "/webhook"
RENDER_EXTERNAL_HOSTNAME = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
if not RENDER_EXTERNAL_HOSTNAME:
    raise ValueError("RENDER_EXTERNAL_HOSTNAME topilmadi")
WEBHOOK_URL = f"https://{RENDER_EXTERNAL_HOSTNAME}{WEBHOOK_PATH}"

CHANNEL_USERNAME = "@mega_kino_n1"


# ======================== Reklama ========================
async def send_ad(bot, chat_id):
    ad = await get_ad()
    if not ad:
        return
    content_type = ad["content_type"]
    file_id = ad["file_id"]
    text = ad["text"]
    caption = ad["caption"] or ""
    try:
        if content_type == "text":
            await bot.send_message(chat_id=chat_id, text=text)
        elif content_type == "photo":
            await bot.send_photo(chat_id=chat_id, photo=file_id, caption=caption)
        elif content_type == "video":
            await bot.send_video(chat_id=chat_id, video=file_id, caption=caption)
        elif content_type == "document":
            await bot.send_document(chat_id=chat_id, document=file_id, caption=caption)
        elif content_type == "audio":
            await bot.send_audio(chat_id=chat_id, audio=file_id, caption=caption)
        elif content_type == "voice":
            await bot.send_voice(chat_id=chat_id, voice=file_id, caption=caption)
        elif content_type == "animation":
            await bot.send_animation(chat_id=chat_id, animation=file_id, caption=caption)
        await increment_ad_count()
    except Exception as e:
        print(f"Reklama yuborishda xatolik: {e}")


# ======================== Telegram a'zolik tekshiruvi ========================
async def check_telegram_membership(bot, user_id, sub_data):
    try:
        chat_id = None

        if sub_data.get("chat_id"):
            chat_id = sub_data["chat_id"]
        else:
            identifier = sub_data["identifier"]

            if identifier.startswith("@"):
                chat_id = identifier
            elif "t.me/" in identifier:
                if "t.me/+" in identifier or "joinchat" in identifier:
                    try:
                        chat = await bot.get_chat(identifier)
                        chat_id = chat.id
                    except Exception as e:
                        print(f"Zayafka linkdan chat olishda xatolik: {e}")
                        return None
                else:
                    parts = identifier.split("/")
                    if len(parts) >= 2:
                        chat_id = "@" + parts[-1]
            else:
                chat_id = "@" + identifier.lstrip("@")

        if not chat_id:
            return None

        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return member.status in ["member", "administrator", "creator"]

    except Exception as e:
        print(f"Membership check error: {e}")
        return False


# ======================== Majburiy obuna interfeysi ========================
async def show_mandatory_subs(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    subs = await get_active_mandatory_subs()
    if not subs:
        return True

    incomplete = []
    for sub in subs:
        is_completed = await is_user_completed_sub(user_id, sub["id"])
        if not is_completed:
            incomplete.append(sub)

    if not incomplete:
        return True

    text = "🔔 <b>Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:</b>\n\n"
    url_buttons = []

    for idx, sub in enumerate(incomplete, start=1):
        sub_type = sub["type"]
        identifier = sub["identifier"]
        button_text = f"📢 {idx}-kanal"

        if sub_type in ("telegram", "group"):
            if identifier.startswith("@"):
                url = f"https://t.me/{identifier[1:]}"
            elif identifier.startswith("https://"):
                url = identifier
            else:
                url = f"https://t.me/{identifier}"
        elif sub_type == "invite":
            url = identifier
        elif sub_type == "bot":
            bot_username = identifier.replace("@", "").replace("https://t.me/", "").split("?")[0].split("/")[-1]
            url = f"https://t.me/{bot_username}?start=start"
        elif sub_type in ("youtube", "instagram", "website"):
            url = identifier
        else:
            url = identifier

        url_buttons.append([InlineKeyboardButton(button_text, url=url)])

    confirm_button = [[InlineKeyboardButton("✅ Obuna bo'ldim", callback_data="confirm_all_subs")]]
    reply_markup = InlineKeyboardMarkup(url_buttons + confirm_button)

    if "mandatory_msg_id" in context.user_data:
        try:
            await context.bot.delete_message(
                chat_id=user_id, message_id=context.user_data["mandatory_msg_id"]
            )
        except Exception:
            pass

    if update.callback_query:
        sent_msg = await context.bot.send_message(
            chat_id=user_id, text=text, reply_markup=reply_markup,
            parse_mode="HTML", disable_web_page_preview=True
        )
    else:
        sent_msg = await update.message.reply_text(
            text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True
        )

    context.user_data["mandatory_msg_id"] = sent_msg.message_id
    return False


# ======================== Majburiy obuna tekshiruvi ========================
async def check_and_handle_mandatory_subs(update: Update, context: CallbackContext):
    user_id = update.effective_user.id

    cache_key = "sub_check_cache"
    cache_time_key = "sub_check_time"
    current_time = time.time()

    if cache_time_key in context.user_data:
        if current_time - context.user_data[cache_time_key] < 5:
            if context.user_data.get(cache_key, False):
                await show_mandatory_subs(update, context)
                return True
            return False

    subs = await get_active_mandatory_subs()
    if not subs:
        context.user_data[cache_key] = False
        context.user_data[cache_time_key] = current_time
        return False

    telegram_types = ["telegram", "group", "invite"]

    async def check_sub(sub):
        already_completed = await is_user_completed_sub(user_id, sub["id"])

        if sub["type"] in telegram_types:
            result = await check_telegram_membership(context.bot, user_id, sub)

            if result is True:
                if not already_completed:
                    await mark_user_completed_sub(user_id, sub["id"])
                return (sub, True)
            elif result is False:
                if already_completed:
                    await set_user_completed_sub(user_id, sub["id"], False)
                return (sub, False)
            else:
                return (sub, already_completed)
        else:
            return (sub, already_completed)

    results = await asyncio.gather(*[check_sub(sub) for sub in subs])

    incomplete = [sub for sub, is_ok in results if not is_ok]

    context.user_data[cache_time_key] = current_time
    if incomplete:
        context.user_data[cache_key] = True
        await show_mandatory_subs(update, context)
        return True
    else:
        context.user_data[cache_key] = False
        return False


# ======================== Callback: obunani tasdiqlash ========================
async def confirm_all_subs_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    print(f"\n🔴 ========== CONFIRM_CALLBACK ==========")
    print(f"🔴 user_id = {user_id}")

    subs = await get_active_mandatory_subs()
    print(f"🔴 subs soni = {len(subs) if subs else 0}")

    if not subs:
        await query.edit_message_text("✅ Hech qanday majburiy obuna mavjud emas.")
        await start_after_subs(update, context)
        return

    for s in subs:
        print(f"🔴   sub: id={s['id']}, type={s['type']}, identifier={s['identifier']}")

    still_incomplete = []
    for sub in subs:
        is_completed = await is_user_completed_sub(user_id, sub["id"])
        print(f"🔴 is_user_completed_sub({user_id}, {sub['id']}) = {is_completed}")
        if not is_completed:
            still_incomplete.append(sub)

    print(f"🔴 still_incomplete = {len(still_incomplete)}")

    if not still_incomplete:
        context.user_data.pop("sub_check_cache", None)
        context.user_data.pop("sub_check_time", None)
        context.user_data.pop("mandatory_msg_id", None)
        await query.edit_message_text("✅ Barcha kanallarga obuna bo'lgansiz!")
        await start_after_subs(update, context)
        return

    telegram_types = ["telegram", "group", "invite"]

    async def check_single_sub(sub):
        if sub["type"] in telegram_types:
            result = await check_telegram_membership(context.bot, user_id, sub)
            print(f"🔴 telegram check: id={sub['id']}, result={result}")
            if result is None:
                return (sub, True)
            return (sub, result)
        else:
            print(f"🔴 non-telegram True: id={sub['id']}, type={sub['type']}")
            return (sub, True)

    results = await asyncio.gather(*[check_single_sub(sub) for sub in still_incomplete])
    print(f"🔴 results = {[(r[0]['id'], r[1]) for r in results]}")

    sub_positions = {s["id"]: i for i, s in enumerate(subs, start=1)}
    failed = [f"❌ {sub_positions.get(sub['id'], '?')}-kanal" for sub, is_ok in results if not is_ok]
    print(f"🔴 failed = {failed}")

    if failed:
        msg_text = (
            "Quyidagi kanallarga obuna bo'lmagansiz:\n\n" +
            "\n".join(failed) +
            "\n\nIltimos, avval ularga obuna bo'ling va qayta tekshiring."
        )
        await query.edit_message_text(msg_text, disable_web_page_preview=True)
        return

    for sub in still_incomplete:
        print(f"🔴 mark_user_completed_sub: user={user_id}, sub={sub['id']}")
        try:
            result = await mark_user_completed_sub(user_id, sub["id"])
            print(f"🔴   natija = {result}")
        except Exception as e:
            print(f"🔴   XATO: {e}")

    context.user_data.pop("sub_check_cache", None)
    context.user_data.pop("sub_check_time", None)
    context.user_data.pop("mandatory_msg_id", None)

    await query.edit_message_text("✅ Ajoyib! Barcha kanallarga obuna bo'lgansiz. Botdan foydalanishingiz mumkin!")

    await start_after_subs(update, context)
    print(f"🔴 ========== TUGADI ==========\n")


async def start_after_subs(update: Update, context: CallbackContext):
    user_id = update.effective_user.id

    await context.bot.send_message(
        chat_id=user_id,
        text=(
            "🎬 Kino botiga xush kelibsiz!\n"
            f"📣 Kino kanalimiz: {CHANNEL_USERNAME}\n\n"
            "Film kodini raqamlarda botga yuboring.\n"
            "Admin: /admin"
        )
    )
    asyncio.create_task(send_ad(context.bot, user_id))


# ======================== Start ========================
async def start(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    referral_code = context.args[0] if context.args else None
    await register_user_start(user_id, referral_code)

    if await check_and_handle_mandatory_subs(update, context):
        return

    await start_after_subs(update, context)


# ======================== Admin panel ========================
async def admin(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Siz admin emassiz!")
        return
    await update.message.reply_text(
        "<b>🔧 Admin panel</b>\n\n"
        "/addvideo - yangi video qo'shish\n"
        "/delvideo &lt;kod&gt; - o'chirish\n"
        "/list - barcha videolar\n"
        "/stats - statistika\n"
        "/broadcast - obunachilarga xabar\n"
        "/createref - referal havola yaratish\n"
        "/refstats - referallar statistikasi\n"
        "/setad - reklama o'rnatish\n"
        "/removead - reklamani o'chirish\n"
        "/adstats - reklama statistikasi\n\n"
        "<b>📛 Majburiy obuna:</b>\n"
        "/add_mandatory &lt;tur&gt; &lt;havola&gt; &lt;limit&gt; [chat_id]\n"
        "/remove_mandatory &lt;id&gt;\n"
        "/list_mandatory\n\n"
        "<b>Turlar:</b> telegram, group, invite, bot, youtube, instagram, website",
        parse_mode="HTML",
        disable_web_page_preview=True
    )


# ======================== Statistika ========================
async def stats(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        return
    total = await get_total_users()
    today = await get_today_users()
    week = await get_week_users()
    active = await get_active_users_last_24h()
    await update.message.reply_text(
        f"📊 Statistika\n\n"
        f"👥 Umumiy: {total}\n"
        f"🆕 Bugun: {today}\n"
        f"📅 7 kunda: {week}\n"
        f"🟢 24 soatda faol: {active}"
    )


# ======================== Broadcast ========================
async def broadcast_start(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    await update.message.reply_text(
        "📢 Barcha obunachilarga yubormoqchi bo'lgan xabaringizni yuboring.\n"
        "/cancel – bekor qilish"
    )
    return WAITING_BROADCAST


async def broadcast_send(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    msg = update.message
    user_ids = await get_all_user_ids()
    total = len(user_ids)
    progress_msg = await msg.reply_text(f"📤 {total} ta foydalanuvchiga jo'natish boshlandi...")
    asyncio.create_task(_broadcast_task(msg, progress_msg, user_ids, total))
    return ConversationHandler.END


async def _broadcast_task(msg, progress_msg, user_ids, total):
    semaphore = asyncio.Semaphore(25)

    async def send_to_user(uid):
        async with semaphore:
            try:
                await msg.copy(chat_id=uid)
            except Exception:
                pass

    tasks = [asyncio.create_task(send_to_user(uid)) for uid in user_ids]
    await asyncio.gather(*tasks)
    await progress_msg.edit_text(f"✅ Xabar {total} ta foydalanuvchiga yuborildi.")


# ======================== Video qo'shish ========================
async def addvideo_start(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    await update.message.reply_text("📹 Videoni yuboring (fayl sifatida)")
    return WAITING_FOR_VIDEO


async def addvideo_video(update: Update, context: CallbackContext):
    if not update.message.video:
        await update.message.reply_text("❌ Iltimos, video fayl yuboring")
        return WAITING_FOR_VIDEO
    file_id = update.message.video.file_id
    context.user_data['file_id'] = file_id
    await update.message.reply_text("🔢 Kod kiriting (faqat raqamlar):")
    return WAITING_FOR_CUSTOM_CODE


async def addvideo_custom_code(update: Update, context: CallbackContext):
    code = update.message.text.strip()
    if not code.isdigit():
        await update.message.reply_text("❌ Kod faqat raqamlardan iborat bo'lishi kerak:")
        return WAITING_FOR_CUSTOM_CODE
    existing = await get_video(code)
    if existing:
        await update.message.reply_text(f"⚠️ {code} kodi mavjud. Boshqa kod kiriting:")
        return WAITING_FOR_CUSTOM_CODE
    context.user_data['code'] = code
    await update.message.reply_text("✍️ Tavsif yozing (yoki /skip)")
    return WAITING_FOR_DESCRIPTION


async def addvideo_description(update: Update, context: CallbackContext):
    description = update.message.text
    file_id = context.user_data.get('file_id')
    code = context.user_data.get('code')
    if not file_id or not code:
        return ConversationHandler.END
    await add_video(code, file_id, description)
    await update.message.reply_text(f"✅ Video saqlandi!\nKod: {code}\nTavsif: {description}")
    context.user
