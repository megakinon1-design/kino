import os
import asyncio
import secrets
import time
import httpx
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.requests import Request
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ConversationHandler, CallbackContext, TypeHandler
)
from dotenv import load_dotenv

from config import BOT_TOKEN, ADMIN_ID
from database import (
    init_db, add_video, get_video, delete_video, list_all_videos,
    register_user_start, get_total_users, get_today_users,
    get_week_users, get_active_users_last_24h,
    get_all_user_ids, create_referral, check_referral_code, get_all_referrals,
    set_ad, get_ad, remove_ad, increment_ad_count,
    get_user_referral_count
)

load_dotenv()


# ======================== safe_task ========================
def safe_task(coro):
    task = asyncio.create_task(coro)

    def _log_exc(t):
        try:
            t.result()
        except Exception as e:
            print(f"❌ Background task xatosi: {e}")

    task.add_done_callback(_log_exc)
    return task


# ======================== GLOBAL CACHE'LAR ========================
_ad_cache = {"data": None, "timestamp": 0, "ttl": 120}
_last_activity_cache = {}
ACTIVITY_UPDATE_INTERVAL = 300


async def get_cached_ad():
    now = time.time()
    if _ad_cache["data"] is None or now - _ad_cache["timestamp"] > _ad_cache["ttl"]:
        _ad_cache["data"] = await get_ad()
        _ad_cache["timestamp"] = now
    return _ad_cache["data"]


def invalidate_ad_cache():
    _ad_cache["data"] = None
    _ad_cache["timestamp"] = 0


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

# ======================== Bot sozlamalari ========================
BOT_USERNAME = "@Kinolarolami7bot"
CHANNEL_USERNAME = "@kinolar_olami_i7"
CHANNEL_URL = "https://t.me/kinolar_olami_i7"


# ======================== SELF-PING ========================
async def self_ping():
    await asyncio.sleep(30)
    ping_count = 0
    while True:
        ping_count += 1
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"https://{RENDER_EXTERNAL_HOSTNAME}/healthcheck")
                print(f"💓 Self-ping #{ping_count}: {resp.status_code} ({time.strftime('%H:%M:%S')})")
        except Exception as e:
            print(f"⚠️ Self-ping xatosi #{ping_count}: {e}")
        await asyncio.sleep(60)


# ======================== WEBHOOK WATCHDOG ========================
async def webhook_watchdog():
    while True:
        await asyncio.sleep(240)
        try:
            info = await bot_application.bot.get_webhook_info()
            if info.pending_update_count > 10 or info.last_error_message:
                print(f"⚠️ Webhook muammosi: pending={info.pending_update_count}, error={info.last_error_message}")
                await bot_application.bot.set_webhook(
                    url=WEBHOOK_URL,
                    drop_pending_updates=True,
                    allowed_updates=["message", "callback_query"]
                )
                print("✅ Webhook qayta o'rnatildi")
            else:
                print(f"💚 Webhook sog'lom: pending={info.pending_update_count} ({time.strftime('%H:%M:%S')})")
        except Exception as e:
            print(f"⚠️ Watchdog xatosi: {e}")


# ======================== Middleware: activity tracking ========================
async def track_activity(update: Update, context: CallbackContext):
    if not update.effective_user:
        return
    user = update.effective_user
    if user.is_bot:
        return
    user_id = user.id
    now = time.time()
    last = _last_activity_cache.get(user_id, 0)
    if now - last < ACTIVITY_UPDATE_INTERVAL:
        return
    _last_activity_cache[user_id] = now
    try:
        await register_user_start(user_id)
    except Exception as e:
        print(f"track_activity xatosi: {e}")


# ======================== Reklama ========================
async def send_ad(bot, chat_id):
    ad = await get_cached_ad()
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


# ======================== Start ========================
async def start(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    referral_code = context.args[0] if context.args else None
    await register_user_start(user_id, referral_code)

    await context.bot.send_message(
        chat_id=user_id,
        text=(
            f"🎬 Kino botiga xush kelibsiz!\n"
            f"📣 Kino kanalimiz: {CHANNEL_USERNAME}\n\n"
            f"Film kodini raqamlarda yuboring.\n"
            f"Admin: /admin\n\n"
            f"🔗 /referral - referal havolangiz va statistikangiz"
        )
    )
    safe_task(send_ad(context.bot, user_id))


# ======================== Referal (foydalanuvchi) ========================
async def referral(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    count = await get_user_referral_count(user_id)
    refer_link = f"https://t.me/{BOT_USERNAME}?start={user_id}"

    text = (
        f"🔗 <b>Sizning referal havolangiz:</b>\n"
        f"<code>{refer_link}</code>\n\n"
        f"👥 <b>Umumiy qo'shgan odamlaringiz:</b> {count} ta\n\n"
        f"<i>Havolani do'stlaringizga yuboring va botga qo'shilingan har bir do'stingiz hisoblanadi!</i>"
    )
    await update.message.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)


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
        "/userref &lt;user_id&gt; - foydalanuvchi referallari\n"
        "/setad - reklama o'rnatish\n"
        "/removead - reklamani o'chirish\n"
        "/adstats - reklama statistikasi",
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


# ======================== Foydalanuvchi referallari (admin) ========================
async def userref(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("📛 Foydalanuvchi ID: /userref 123456789")
        return
    try:
        target_user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Noto'g'ri ID formati.")
        return
    count = await get_user_referral_count(target_user_id)
    refer_link = f"https://t.me/{BOT_USERNAME}?start={target_user_id}"
    text = (
        f"🔗 <b>Foydalanuvchi {target_user_id} referal havolasi:</b>\n"
        f"<code>{refer_link}</code>\n\n"
        f"👥 <b>Umumiy qo'shgan odamlari:</b> {count} ta"
    )
    await update.message.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)


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
    safe_task(_broadcast_task(msg, progress_msg, user_ids, total))
    return ConversationHandler.END


async def _broadcast_task(msg, progress_msg, user_ids, total):
    semaphore = asyncio.Semaphore(25)
    sent = 0
    failed = 0

    async def send_to_user(uid):
        nonlocal sent, failed
        async with semaphore:
            try:
                await msg.copy(chat_id=uid)
                sent += 1
            except Exception:
                failed += 1

    tasks = [asyncio.create_task(send_to_user(uid)) for uid in user_ids]
    await asyncio.gather(*tasks)

    try:
        await progress_msg.edit_text(
            f"✅ Yuborildi: {sent}/{total}\n"
            f"❌ Xato: {failed}"
        )
    except Exception:
        pass


# ======================== Video qo'shish ========================
async def addvideo_start(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    await update.message.reply_text(
        "📹 <b>Videoni yuboring</b>\n\n"
        "💡 Video yoki File sifatida yuboring.",
        parse_mode="HTML"
    )
    return WAITING_FOR_VIDEO


async def addvideo_video(update: Update, context: CallbackContext):
    msg = update.message
    file_id = None

    if msg.video:
        file_id = msg.video.file_id
    elif msg.document and msg.document.mime_type and msg.document.mime_type.startswith("video/"):
        file_id = msg.document.file_id
    elif msg.animation:
        file_id = msg.animation.file_id
    else:
        await msg.reply_text(
            "❌ Iltimos, video fayl yuboring.\n\n"
            "💡 Maslahat: Videoni <b>Video</b> yoki <b>File</b> sifatida yuboring.",
            parse_mode="HTML"
        )
        return WAITING_FOR_VIDEO

    context.user_data['file_id'] = file_id
    await msg.reply_text("🔢 Kod kiriting (faqat raqamlar):")
    return WAITING_FOR_CUSTOM_CODE


async def addvideo_custom_code(update: Update, context: CallbackContext):
    if not update.message or not update.message.text:
        await update.message.reply_text("❌ Iltimos, matn yuboring.")
        return WAITING_FOR_CUSTOM_CODE

    code = update.message.text.strip()
    if code.startswith("/"):
        code = code[1:]

    if not code.isdigit():
        await update.message.reply_text("❌ Kod faqat raqamlardan iborat bo'lishi kerak. Qaytadan kiriting:")
        return WAITING_FOR_CUSTOM_CODE

    existing = await get_video(code)
    if existing:
        await update.message.reply_text(f"⚠️ {code} kodi mavjud. Boshqa kod kiriting:")
        return WAITING_FOR_CUSTOM_CODE

    context.user_data['code'] = code
    await update.message.reply_text(
        f"✍️ Kod: <b>{code}</b>\n\nEndi tavsif yozing yoki /skip yuboring:",
        parse_mode="HTML"
    )
    return WAITING_FOR_DESCRIPTION


async def addvideo_description(update: Update, context: CallbackContext):
    if not update.message or not update.message.text:
        await update.message.reply_text("❌ Iltimos, matn yuboring yoki /skip bosing.")
        return WAITING_FOR_DESCRIPTION

    description = update.message.text.strip()
    file_id = context.user_data.get('file_id')
    code = context.user_data.get('code')

    if not file_id or not code:
        await update.message.reply_text("❌ Xatolik: video yoki kod topilmadi. Qaytadan /addvideo bosing.")
        context.user_data.pop('file_id', None)
        context.user_data.pop('code', None)
        return ConversationHandler.END

    await add_video(code, file_id, description)
    await update.message.reply_text(
        f"✅ <b>Video saqlandi!</b>\n\n"
        f"🔢 Kod: <code>{code}</code>\n"
        f"📖 Tavsif: {description}",
        parse_mode="HTML"
    )
    context.user_data.pop('file_id', None)
    context.user_data.pop('code', None)
    return ConversationHandler.END


async def addvideo_skip(update: Update, context: CallbackContext):
    file_id = context.user_data.get('file_id')
    code = context.user_data.get('code')

    if not file_id or not code:
        await update.message.reply_text("❌ Xatolik: video yoki kod topilmadi. Qaytadan /addvideo bosing.")
        context.user_data.pop('file_id', None)
        context.user_data.pop('code', None)
        return ConversationHandler.END

    await add_video(code, file_id, "")
    await update.message.reply_text(
        f"✅ <b>Video saqlandi!</b>\n\n"
        f"🔢 Kod: <code>{code}</code>\n"
        f"📖 Tavsif: yo'q",
        parse_mode="HTML"
    )
    context.user_data.pop('file_id', None)
    context.user_data.pop('code', None)
    return ConversationHandler.END


async def cancel(update: Update, context: CallbackContext):
    context.user_data.pop('file_id', None)
    context.user_data.pop('code', None)
    await update.message.reply_text("❌ Bekor qilindi.")
    return ConversationHandler.END


# ======================== Video o'chirish ========================
async def delvideo(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("📛 Kodni kiriting: /delvideo 123")
        return
    code = context.args[0].strip()
    if not code.isdigit():
        await update.message.reply_text("❌ Kod faqat raqamlardan iborat bo'lishi kerak.")
        return
    video = await get_video(code)
    if video:
        await delete_video(code)
        await update.message.reply_text(f"✅ {code} o'chirildi.")
    else:
        await update.message.reply_text(f"❌ {code} topilmadi.")


async def listvideos(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        return
    videos = await list_all_videos()
    if not videos:
        await update.message.reply_text("📭 Hech qanday video yo'q.")
        return

    text = "📋 Barcha videolar:\n\n"
    for code, desc in videos:
        line = f"🔹 {code} — {desc or 'Tavsifsiz'}\n"
        if len(text) + len(line) > 4000:
            await update.message.reply_text(text)
            text = ""
        text += line
    if text.strip():
        await update.message.reply_text(text)


# ======================== Referal (admin) ========================
async def createref_start(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    await update.message.reply_text("🔗 Referal uchun nom bering:")
    return WAITING_REF_NAME


async def createref_get_name(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    name = update.message.text.strip() if update.message.text else ""
    if not name:
        await update.message.reply_text("❌ Bo'sh bo'lmagan nom kiriting.")
        return WAITING_REF_NAME
    while True:
        code = secrets.token_hex(3)
        if not await check_referral_code(code):
            break
    await create_referral(name, code)
    link = f"https://t.me/{BOT_USERNAME}?start={code}"
    await update.message.reply_text(
        f"✅ Yangi referal havola yaratildi\n\n"
        f"📌 Nomi: {name}\n"
        f"🔗 Havola: {link}\n"
        f"🆔 Kod: {code}"
    )
    return ConversationHandler.END


async def refstats(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        return
    referrals = await get_all_referrals()
    if not referrals:
        await update.message.reply_text("📭 Hali hech qanday referal havola yo'q.")
        return
    text = "📊 Referallar statistikasi\n\n"
    for code, name, count in referrals:
        text += f"• {name} (kod: {code}) – {count} ta\n"
    await update.message.reply_text(text)


# ======================== Reklama ========================
async def setad_start(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    await update.message.reply_text("📢 Reklama kontentini yuboring.\n/cancel – bekor qilish")
    return WAITING_AD_CONTENT


async def setad_get_content(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    msg = update.message
    content_type = None
    file_id = None
    text = None
    caption = msg.caption or ""

    if msg.text and not msg.caption:
        content_type = "text"
        text = msg.text
    elif msg.photo:
        content_type = "photo"
        file_id = msg.photo[-1].file_id
    elif msg.video:
        content_type = "video"
        file_id = msg.video.file_id
    elif msg.document:
        content_type = "document"
        file_id = msg.document.file_id
    elif msg.audio:
        content_type = "audio"
        file_id = msg.audio.file_id
    elif msg.voice:
        content_type = "voice"
        file_id = msg.voice.file_id
    elif msg.animation:
        content_type = "animation"
        file_id = msg.animation.file_id
    else:
        await update.message.reply_text("❌ Qo'llab-quvvatlanmaydi. Boshqa narsa yuboring.")
        return WAITING_AD_CONTENT

    await set_ad(content_type, file_id, text, caption)
    invalidate_ad_cache()
    await update.message.reply_text(f"✅ Reklama saqlandi!\nTuri: {content_type}")
    return ConversationHandler.END


async def removead(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        return
    await remove_ad()
    invalidate_ad_cache()
    await update.message.reply_text("🗑️ Reklama o'chirildi.")


async def adstats(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        return
    ad = await get_cached_ad()
    if ad:
        await update.message.reply_text(f"📊 Reklama {ad['send_count']} marta yuborilgan.")
    else:
        await update.message.reply_text("📭 Reklama o'rnatilmagan.")


# ======================== Kod yuborish ========================
async def handle_code(update: Update, context: CallbackContext):
    user_id = update.effective_user.id

    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("🤔 Iltimos, faqat raqamlardan iborat kod yuboring.")
        return

    video = await get_video(text)
    if video:
        file_id, description = video
        caption = f"🎬 Kodi: {text}\n📖 {description}" if description else f"🎬 Kodi: {text}"
        try:
            await update.message.reply_video(
                video=file_id, caption=caption, supports_streaming=True, protect_content=True
            )
        except Exception as e:
            print(f"Video yuborish xatosi: {e}")
            await update.message.reply_text("❌ Video yuborishda xatolik yuz berdi.")
            return
        links_msg = (
            f"📱 Instagram: https://www.instagram.com/kino_sevarlar\n"
            f"📣 Kino kanal: {CHANNEL_USERNAME}"
        )
        await update.message.reply_text(links_msg)
        safe_task(send_ad(context.bot, user_id))
    else:
        await update.message.reply_text(f"❌ {text} kodli video topilmadi.")


# ======================== Webhook ========================
async def safe_process_update(update):
    try:
        await bot_application.process_update(update)
    except Exception as e:
        print(f"❌ process_update xatosi: {e}")


async def webhook_handler(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, bot_application.bot)
        asyncio.create_task(safe_process_update(update))
    except Exception as e:
        print(f"❌ webhook_handler xatosi: {e}")
    return JSONResponse({"ok": True})


async def healthcheck(request: Request):
    try:
        bot_username = bot_application.bot.username if bot_application else None
    except Exception:
        bot_username = None
    return JSONResponse({"status": "ok", "bot": bot_username, "time": time.time()})


bot_application = None


# ======================== Main ========================
async def main():
    global bot_application
    await init_db()

    bot_application = Application.builder().token(BOT_TOKEN).build()
    private_filter = filters.ChatType.PRIVATE

    # Activity tracker (eng birinchi)
    bot_application.add_handler(TypeHandler(Update, track_activity), group=-1)

    # Asosiy komandalar
    bot_application.add_handler(CommandHandler("start", start, filters=private_filter))
    bot_application.add_handler(CommandHandler("admin", admin, filters=private_filter))
    bot_application.add_handler(CommandHandler("stats", stats, filters=private_filter))
    bot_application.add_handler(CommandHandler("delvideo", delvideo, filters=private_filter))
    bot_application.add_handler(CommandHandler("list", listvideos, filters=private_filter))
    bot_application.add_handler(CommandHandler("refstats", refstats, filters=private_filter))
    bot_application.add_handler(CommandHandler("referral", referral, filters=private_filter))
    bot_application.add_handler(CommandHandler("userref", userref, filters=private_filter))
    bot_application.add_handler(CommandHandler("removead", removead, filters=private_filter))
    bot_application.add_handler(CommandHandler("adstats", adstats, filters=private_filter))
    bot_application.add_handler(CommandHandler("cancel", cancel, filters=private_filter))

    # ======================== addvideo ========================
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("addvideo", addvideo_start, filters=private_filter)],
        states={
            WAITING_FOR_VIDEO: [
                MessageHandler(
                    (filters.VIDEO | filters.Document.VIDEO | filters.ANIMATION) & private_filter,
                    addvideo_video
                ),
                MessageHandler(
                    filters.ALL & ~filters.COMMAND & private_filter,
                    lambda u, c: u.message.reply_text(
                        "❌ Iltimos, video yuboring (Video yoki File sifatida)."
                    )
                )
            ],
            WAITING_FOR_CUSTOM_CODE: [
                MessageHandler(filters.TEXT & private_filter, addvideo_custom_code)
            ],
            WAITING_FOR_DESCRIPTION: [
                CommandHandler("skip", addvideo_skip, filters=private_filter),
                MessageHandler(filters.TEXT & private_filter, addvideo_description)
            ]
        },
        fallbacks=[
            CommandHandler("cancel", cancel, filters=private_filter),
            CommandHandler("start", start, filters=private_filter),
        ],
        conversation_timeout=300,
        allow_reentry=True,
        per_message=False,
    )
    bot_application.add_handler(conv_handler)

    # ======================== broadcast ========================
    broadcast_conv = ConversationHandler(
        entry_points=[CommandHandler("broadcast", broadcast_start, filters=private_filter)],
        states={
            WAITING_BROADCAST: [
                MessageHandler(filters.ALL & ~filters.COMMAND & private_filter, broadcast_send)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel, filters=private_filter)],
        conversation_timeout=300,
        allow_reentry=True,
        per_message=False,
    )
    bot_application.add_handler(broadcast_conv)

    # ======================== createref ========================
    ref_conv = ConversationHandler(
        entry_points=[CommandHandler("createref", createref_start, filters=private_filter)],
        states={
            WAITING_REF_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & private_filter, createref_get_name)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel, filters=private_filter)],
        conversation_timeout=300,
        allow_reentry=True,
        per_message=False,
    )
    bot_application.add_handler(ref_conv)

    # ======================== setad ========================
    ad_conv = ConversationHandler(
        entry_points=[CommandHandler("setad", setad_start, filters=private_filter)],
        states={
            WAITING_AD_CONTENT: [
                MessageHandler(filters.ALL & ~filters.COMMAND & private_filter, setad_get_content)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel, filters=private_filter)],
        conversation_timeout=300,
        allow_reentry=True,
        per_message=False,
    )
    bot_application.add_handler(ad_conv)

    # Oxirgi handler (kod yuborish)
    bot_application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND & private_filter, handle_code)
    )

    # ======================== Initialize ========================
    max_retries = 10
    for attempt in range(1, max_retries + 1):
        try:
            print(f"🔄 Initialize urinish {attempt}/{max_retries}...")
            await bot_application.initialize()
            print("✅ Initialize muvaffaqiyatli")
            break
        except Exception as e:
            print(f"⚠️ Initialize xatosi ({attempt}/{max_retries}): {e}")
            if attempt == max_retries:
                raise
            wait = min(5 * attempt, 30)
            await asyncio.sleep(wait)

    # ======================== Webhook ========================
    for attempt in range(1, 6):
        try:
            await bot_application.bot.set_webhook(
                url=WEBHOOK_URL,
                drop_pending_updates=True,
                allowed_updates=["message", "callback_query"]
            )
            print("✅ Webhook o'rnatildi")
            break
        except Exception as e:
            print(f"⚠️ Webhook xatosi ({attempt}/5): {e}")
            if attempt == 5:
                raise
            await asyncio.sleep(5)

    try:
        info = await bot_application.bot.get_webhook_info()
        print(f"📡 Webhook URL: {info.url}")
        print(f"📡 Pending updates: {info.pending_update_count}")
        if info.last_error_message:
            print(f"⚠️ Oxirgi xato: {info.last_error_message}")
        else:
            print("✅ Webhook xatosiz ishlayapti")
    except Exception as e:
        print(f"⚠️ Webhook info xatosi: {e}")

    # ======================== SELF-PING + WATCHDOG ========================
    asyncio.create_task(self_ping())
    asyncio.create_task(webhook_watchdog())
    print("💓 Self-ping va watchdog ishga tushdi")

    starlette_app = Starlette(debug=False, routes=[
        Route(WEBHOOK_PATH, webhook_handler, methods=["POST"]),
        Route("/healthcheck", healthcheck, methods=["GET"]),
        Route("/", healthcheck, methods=["GET"]),
    ])

    port = int(os.environ.get("PORT", 8080))
    print(f"✅ Bot ishga tushdi, webhook: {WEBHOOK_URL}")
    import uvicorn
    config = uvicorn.Config(starlette_app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
