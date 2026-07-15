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
    register_user_start, get_total_users, get_today_new_users,
    get_today_active_users, get_week_users, get_active_users_last_24h, update_user_activity,
    get_all_user_ids, create_referral, check_referral_code, get_all_referrals,
    set_ad, get_ad, remove_ad, increment_ad_count,
    get_active_mandatory_subs, is_user_completed_sub, mark_user_completed_sub,
    add_mandatory_subscription, remove_mandatory_subscription, list_mandatory_subscriptions,
    set_user_completed_sub
)

load_dotenv()

# -------------------- Holatlar --------------------
WAITING_FOR_VIDEO, WAITING_FOR_CUSTOM_CODE, WAITING_FOR_DESCRIPTION = range(3)
WAITING_BROADCAST = 3
WAITING_REF_NAME = 4
WAITING_AD_CONTENT = 5

# -------------------- Webhook --------------------
WEBHOOK_PATH = "/webhook"
RENDER_EXTERNAL_HOSTNAME = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
if not RENDER_EXTERNAL_HOSTNAME:
    raise ValueError("RENDER_EXTERNAL_HOSTNAME topilmadi")
WEBHOOK_URL = f"https://{RENDER_EXTERNAL_HOSTNAME}{WEBHOOK_PATH}"

# -------------------- Reklama yuborish --------------------
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
        else:
            return
        await increment_ad_count()
    except Exception as e:
        print(f"Reklama yuborishda xatolik: {e}")

# -------------------- Telegram kanal tekshiruvi --------------------
async def check_telegram_membership(bot, user_id, chat_identifier):
    try:
        member = await bot.get_chat_member(chat_id=chat_identifier, user_id=user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception as e:
        print(f"Telegram membership check error: {e}")
        return False

# -------------------- Majburiy obuna interfeysi --------------------
async def show_mandatory_subs(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    subs = await get_active_mandatory_subs()
    if not subs:
        return True

    incomplete = []
    for sub in subs:
        if not await is_user_completed_sub(user_id, sub["id"]):
            incomplete.append(sub)

    if not incomplete:
        return True

    text = "🎬 Botdan foydalanish uchun quyidagi kanallarga a'zo bo'lishingiz kerak:\n\n"
    url_buttons = []
    for idx, sub in enumerate(incomplete, start=1):
        button_text = f"{idx}-kanal"
        url = sub["identifier"]
        if sub["type"] == "telegram" and url.startswith("@"):
            url = f"https://t.me/{url[1:]}"
        url_buttons.append([InlineKeyboardButton(button_text, url=url)])

    confirm_button = [[InlineKeyboardButton("✅ Obunani tasdiqlash", callback_data="confirm_all_subs")]]
    reply_markup = InlineKeyboardMarkup(url_buttons + confirm_button)

    if "mandatory_msg_id" in context.user_data:
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=context.user_data["mandatory_msg_id"])
        except:
            pass
    sent_msg = await update.message.reply_text(text, reply_markup=reply_markup)
    context.user_data["mandatory_msg_id"] = sent_msg.message_id
    return False

# --- Majburiy obunani real vaqtda tekshirish ---
async def check_and_handle_mandatory_subs(update: Update, context: CallbackContext):
    user_id = update.effective_user.id

    cache_key = "sub_check_cache"
    cache_time_key = "sub_check_time"
    current_time = time.time()

    if cache_time_key in context.user_data:
        if current_time - context.user_data[cache_time_key] < 30:
            if context.user_data.get(cache_key, False):
                await show_mandatory_subs(update, context)
                return True
            return False

    subs = await get_active_mandatory_subs()
    if not subs:
        context.user_data[cache_key] = False
        context.user_data[cache_time_key] = current_time
        return False

    async def check_sub(sub):
        if sub["type"] == "telegram":
            identifier = sub["identifier"]
            if not identifier.startswith("@"):
                identifier = "@" + identifier
            is_member = await check_telegram_membership(context.bot, user_id, identifier)
            return (sub, is_member)
        else:
            is_completed = await is_user_completed_sub(user_id, sub["id"])
            return (sub, is_completed)

    results = await asyncio.gather(*[check_sub(sub) for sub in subs])

    incomplete = []
    for sub, is_ok in results:
        if sub["type"] == "telegram":
            if not is_ok:
                await set_user_completed_sub(user_id, sub["id"], False)
                incomplete.append(sub)
            else:
                if not await is_user_completed_sub(user_id, sub["id"]):
                    await mark_user_completed_sub(user_id, sub["id"])
        else:
            if not is_ok:
                incomplete.append(sub)

    context.user_data[cache_time_key] = current_time
    if incomplete:
        context.user_data[cache_key] = True
        await show_mandatory_subs(update, context)
        return True
    else:
        context.user_data[cache_key] = False
        return False

# -------------------- Callback --------------------
async def confirm_all_subs_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    await update_user_activity(user_id)

    subs = await get_active_mandatory_subs()
    if not subs:
        if query.message.text != "Hech qanday majburiy obuna mavjud emas.":
            await query.edit_message_text("Hech qanday majburiy obuna mavjud emas.")
        await start_after_subs(update, context)
        return

    still_incomplete = []
    for sub in subs:
        if not await is_user_completed_sub(user_id, sub["id"]):
            still_incomplete.append(sub)

    if not still_incomplete:
        if query.message.text != "Siz barcha obunalarni avval tasdiqlagansiz.":
            await query.edit_message_text("Siz barcha obunalarni avval tasdiqlagansiz.")
        await start_after_subs(update, context)
        return

    async def check_telegram_sub(sub):
        identifier = sub["identifier"]
        if not identifier.startswith("@"):
            identifier = "@" + identifier
        is_member = await check_telegram_membership(context.bot, user_id, identifier)
        return (sub, is_member)

    telegram_subs = [sub for sub in still_incomplete if sub["type"] == "telegram"]
    results = await asyncio.gather(*[check_telegram_sub(sub) for sub in telegram_subs])

    failed_telegram = []
    for sub, is_member in results:
        if not is_member:
            failed_telegram.append(sub["identifier"])

    if failed_telegram:
        msg_text = (
            f"❌ Siz quyidagi Telegram kanal(lar)ga a'zo emassiz:\n" + "\n".join(failed_telegram) +
            "\n\nIltimos, a'zo bo'ling va qayta urining."
        )
        if query.message.text != msg_text:
            await query.edit_message_text(msg_text)
        return

    deactivated_any = False
    for sub in still_incomplete:
        deactivated = await mark_user_completed_sub(user_id, sub["id"])
        if deactivated:
            deactivated_any = True

    success_text = "✅ Tabriklaymiz! Siz barcha majburiy obunalarni bajardingiz. Endi botdan to'liq foydalanishingiz mumkin."
    if query.message.text != success_text:
        await query.edit_message_text(success_text)
    if "mandatory_msg_id" in context.user_data:
        del context.user_data["mandatory_msg_id"]

    await start_after_subs(update, context)

async def start_after_subs(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if update.callback_query:
        message = update.callback_query.message
    else:
        message = update.message
    await message.reply_text(
        "🎬 Kino botiga xush kelibsiz!\n"
        "📣 Kino kanalimiz: @kino_boru\n\n"
        "Film kodini raqamlarda yuboring.\n"
        "Admin: /admin"
    )
    asyncio.create_task(send_ad(context.bot, user_id))

# -------------------- Start --------------------
async def start(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    await update_user_activity(user_id)

    referral_code = context.args[0] if context.args else None
    await register_user_start(user_id, referral_code)

    if await check_and_handle_mandatory_subs(update, context):
        return

    await start_after_subs(update, context)

# -------------------- Admin panel --------------------
async def admin(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Siz admin emassiz!")
        return
    await update.message.reply_text(
        "<b>🔧 Admin panel</b>\n"
        "/addvideo - yangi video qo'shish\n"
        "/delvideo &lt;kod&gt; - o'chirish\n"
        "/list - barcha videolar\n"
        "/stats - statistika\n"
        "/broadcast - obunachilarga xabar\n"
        "/createref - referal havola yaratish\n"
        "/refstats - referallar statistikasi\n"
        "/setad - start/kino reklama o'rnatish\n"
        "/removead - reklamani o'chirish\n"
        "/adstats - reklama statistikasi\n\n"
        "<b>📛 Majburiy obuna</b>\n"
        "/add_mandatory &lt;type&gt; &lt;identifier&gt; &lt;limit&gt;\n"
        "/remove_mandatory &lt;id&gt;\n"
        "/list_mandatory",
        parse_mode="HTML"
    )

# -------------------- Statistika --------------------
async def stats(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Siz admin emassiz!")
        return
    
    total = await get_total_users()
    today_new = await get_today_new_users()
    today_active = await get_today_active_users()
    week = await get_week_users()
    active = await get_active_users_last_24h()
    
    await update.message.reply_text(
        f"📊 Statistika\n\n"
        f"👥 Umumiy: {total}\n"
        f"🆕 Bugun yangi: {today_new}\n"
        f"📊 Bugun faol: {today_active}\n"
        f"📅 7 kunda faol: {week}\n"
        f"🟢 24 soatda faol: {active}"
    )

# -------------------- Broadcast --------------------
async def broadcast_start(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Siz admin emassiz!")
        return ConversationHandler.END
    await update.message.reply_text(
        "📢 Barcha obunachilarga yubormoqchi bo'lgan xabaringizni yuboring.\n"
        "Matn, rasm, video, link — istalgan kontent.\n"
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

# -------------------- Video qo'shish --------------------
async def addvideo_start(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Ruxsat yo'q")
        return ConversationHandler.END
    await update.message.reply_text("📹 Videoni yuboring (fayl sifatida)")
    return WAITING_FOR_VIDEO

async def addvideo_video(update: Update, context: CallbackContext):
    if not update.message.video:
        await update.message.reply_text("❌ Iltimos, video fayl yuboring")
        return WAITING_FOR_VIDEO
    file_id = update.message.video.file_id
    context.user_data['file_id'] = file_id
    await update.message.reply_text("🔢 Ushbu video uchun kod kiriting (faqat raqamlar):")
    return WAITING_FOR_CUSTOM_CODE

async def addvideo_custom_code(update: Update, context: CallbackContext):
    code = update.message.text.strip()
    if not code.isdigit():
        await update.message.reply_text("❌ Kod faqat raqamlardan iborat bo'lishi kerak. Qaytadan kiriting:")
        return WAITING_FOR_CUSTOM_CODE
    existing = await get_video(code)
    if existing:
        await update.message.reply_text(f"⚠️ {code} kodi allaqachon mavjud. Boshqa kod kiriting:")
        return WAITING_FOR_CUSTOM_CODE
    context.user_data['code'] = code
    await update.message.reply_text("✍️ Tavsif yozing (yoki /skip o'tkazib yuborish)")
    return WAITING_FOR_DESCRIPTION

async def addvideo_description(update: Update, context: CallbackContext):
    description = update.message.text
    file_id = context.user_data.get('file_id')
    code = context.user_data.get('code')
    if not file_id or not code:
        await update.message.reply_text("Xatolik, qaytadan /addvideo bosing")
        return ConversationHandler.END
    await add_video(code, file_id, description)
    await update.message.reply_text(f"✅ Video saqlandi!\nKod: {code}\nTavsif: {description}")
    context.user_data.clear()
    return ConversationHandler.END

async def addvideo_skip(update: Update, context: CallbackContext):
    file_id = context.user_data.get('file_id')
    code = context.user_data.get('code')
    if not file_id or not code:
        await update.message.reply_text("Xatolik, qaytadan /addvideo bosing")
        return ConversationHandler.END
    await add_video(code, file_id, "")
    await update.message.reply_text(f"✅ Video saqlandi!\nKod: {code}\nTavsifsiz")
    context.user_data.clear()
    return ConversationHandler.END

async def cancel(update: Update, context: CallbackContext):
    await update.message.reply_text("Bekor qilindi.")
    return ConversationHandler.END

# -------------------- Video o'chirish --------------------
async def delvideo(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Ruxsat yo'q")
        return
    if not context.args:
        await update.message.reply_text("📛 Kodni kiriting: /delvideo 123")
        return
    code = context.args[0]
    video = await get_video(code)
    if video:
        await delete_video(code)
        await update.message.reply_text(f"✅ {code} o'chirildi.")
    else:
        await update.message.reply_text(f"❌ {code} topilmadi.")

async def listvideos(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Ruxsat yo'q")
        return
    videos = await list_all_videos()
    if not videos:
        await update.message.reply_text("📭 Hech qanday video yo'q.")
        return
    text = "📋 Barcha videolar:\n"
    for code, desc in videos:
        text += f"🔹 Kod: {code} — {desc or 'Tavsifsiz'}\n"
    await update.message.reply_text(text)

# -------------------- Referal tizimi --------------------
async def createref_start(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Siz admin emassiz!")
        return ConversationHandler.END
    await update.message.reply_text("🔗 Referal uchun nom bering (masalan, 'instagram'):")
    return WAITING_REF_NAME

async def createref_get_name(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("❌ Iltimos, bo'sh bo'lmagan nom kiriting.")
        return WAITING_REF_NAME
    bot_username = "KINO_bor_botbot"
    while True:
        code = secrets.token_hex(3)
        if not await check_referral_code(code):
            break
    await create_referral(name, code)
    link = f"https://t.me/{bot_username}?start={code}"
    await update.message.reply_text(
        f"✅ Yangi referal havola yaratildi\n\n"
        f"📌 Nomi: {name}\n"
        f"🔗 Havola: {link}\n"
        f"🆔 Kod: {code}"
    )
    return ConversationHandler.END

async def refstats(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Ruxsat yo'q")
        return
    referrals = await get_all_referrals()
    if not referrals:
        await update.message.reply_text("📭 Hali hech qanday referal havola yo'q.")
        return
    text = "📊 Referallar statistikasi\n\n"
    for code, name, count in referrals:
        text += f"• {name} (kod: {code}) – {count} ta foydalanuvchi\n"
    await update.message.reply_text(text)

# -------------------- Reklama tizimi --------------------
async def setad_start(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Siz admin emassiz!")
        return ConversationHandler.END
    await update.message.reply_text(
        "📢 Reklama sifatida yubormoqchi bo'lgan kontentni yuboring.\n"
        "Matn, rasm, video, hujjat, audio, animatsiya — ixtiyoriy.\n"
        "/cancel – bekor qilish"
    )
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
        await update.message.reply_text("❌ Ushbu kontent turi qo'llab-quvvatlanmaydi. Boshqa narsa yuboring.")
        return WAITING_AD_CONTENT

    await set_ad(content_type, file_id, text, caption)
    await update.message.reply_text(
        f"✅ Reklama saqlandi!\n"
        f"Turi: {content_type}\n"
        f"Endi har bir /start va kino kodidan keyin avtomatik yuboriladi."
    )
    return ConversationHandler.END

async def removead(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Ruxsat yo'q")
        return
    await remove_ad()
    await update.message.reply_text("🗑️ Reklama o'chirildi. Endi start va kodlardan keyin ko'rsatilmaydi.")

async def adstats(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Ruxsat yo'q")
        return
    ad = await get_ad()
    if ad:
        count = ad["send_count"]
        await update.message.reply_text(f"📊 Reklama {count} marta yuborilgan.")
    else:
        await update.message.reply_text("📭 Hozirda hech qanday reklama o'rnatilmagan.")

# -------------------- Majburiy obuna admin buyruqlari --------------------
async def add_mandatory(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        return
    args = context.args
    if len(args) < 3:
        await update.message.reply_text("Ishlatish: /add_mandatory <type> <identifier> <limit>\nMasalan: /add_mandatory telegram @my_channel 2000")
        return
    sub_type, identifier, limit = args[0], args[1], int(args[2])
    if sub_type not in ("telegram", "youtube", "instagram"):
        await update.message.reply_text("❌ type faqat: telegram, youtube, instagram")
        return
    await add_mandatory_subscription(sub_type, identifier, limit)
    await update.message.reply_text(f"✅ Qo'shildi: {sub_type} – {identifier} (limit {limit})")

async def remove_mandatory(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Ishlatish: /remove_mandatory <id>")
        return
    sub_id = int(context.args[0])
    await remove_mandatory_subscription(sub_id)
    await update.message.reply_text(f"✅ ID {sub_id} o'chirildi.")

async def list_mandatory(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        return
    rows = await list_mandatory_subscriptions()
    if not rows:
        await update.message.reply_text("Hech qanday majburiy obuna yo'q.")
        return

    text = "📋 Majburiy obunalar:\n"
    for row in rows:
        id_ = row["id"]
        type_ = row["type"]
        ident = row["identifier"]
        limit_ = row["limit_count"]
        current_ = row["current_count"]
        active_ = row["is_active"]
        status = "✅ faol" if active_ else "❌ faol emas"
        text += f"ID {id_}: {type_} {ident} | limit {limit_} | a'zolar: {current_} | {status}\n"

    await update.message.reply_text(text)

# -------------------- Kod yuborish --------------------
async def handle_code(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    await update_user_activity(user_id)

    if await check_and_handle_mandatory_subs(update, context):
        return

    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("🤔 Iltimos, faqat raqamlardan iborat kod yuboring.")
        return
    video = await get_video(text)
    if video:
        file_id, description = video
        caption = f"🎬 Kodi: {text}\n📖 {description}" if description else f"🎬 Kodi: {text}"
        try:
            await update.message.reply_video(video=file_id, caption=caption, supports_streaming=True, protect_content=True)
        except Exception as e:
            print(f"Video yuborish xatosi: {e}")
            await update.message.reply_text("❌ Video yuborishda xatolik yuz berdi.")
            return
        links_msg = (
            "📱 Instagram: https://instagram.com/Bear_uzb070\n"
            "📣 Kino kanal: @kino_boru"
        )
        await update.message.reply_text(links_msg)
        await send_ad(context.bot, user_id)
    else:
        await update.message.reply_text(f"❌ {text} kodli video topilmadi.")

# -------------------- Webhook --------------------
async def webhook_handler(request: Request):
    data = await request.json()
    update = Update.de_json(data, bot_application.bot)
    await bot_application.process_update(update)
    return JSONResponse({"ok": True})

async def healthcheck(request: Request):
    return JSONResponse({"status": "ok"})

bot_application = None

async def main():
    global bot_application
    await init_db()
    bot_application = Application.builder().token(BOT_TOKEN).build()

    private_filter = filters.ChatType.PRIVATE

    bot_application.add_handler(CommandHandler("start", start, filters=private_filter))
    bot_application.add_handler(CommandHandler("admin", admin, filters=private_filter))
    bot_application.add_handler(CommandHandler("stats", stats, filters=private_filter))
    bot_application.add_handler(CommandHandler("delvideo", delvideo, filters=private_filter))
    bot_application.add_handler(CommandHandler("list", listvideos, filters=private_filter))
    bot_application.add_handler(CommandHandler("refstats", refstats, filters=private_filter))
    bot_application.add_handler(CommandHandler("removead", removead, filters=private_filter))
    bot_application.add_handler(CommandHandler("adstats", adstats, filters=private_filter))
    bot_application.add_handler(CommandHandler("cancel", cancel, filters=private_filter))

    bot_application.add_handler(CommandHandler("add_mandatory", add_mandatory, filters=private_filter))
    bot_application.add_handler(CommandHandler("remove_mandatory", remove_mandatory, filters=private_filter))
    bot_application.add_handler(CommandHandler("list_mandatory", list_mandatory, filters=private_filter))
    bot_application.add_handler(CallbackQueryHandler(confirm_all_subs_callback, pattern="^confirm_all_subs$"))

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("addvideo", addvideo_start, filters=private_filter)],
        states={
            WAITING_FOR_VIDEO: [MessageHandler(filters.VIDEO & private_filter, addvideo_video)],
            WAITING_FOR_CUSTOM_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND & private_filter, addvideo_custom_code)],
            WAITING_FOR_DESCRIPTION: [
                CommandHandler("skip", addvideo_skip, filters=private_filter),
                MessageHandler(filters.TEXT & ~filters.COMMAND & private_filter, addvideo_description)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel, filters=private_filter)]
    )
    bot_application.add_handler(conv_handler)

    broadcast_conv = ConversationHandler(
        entry_points=[CommandHandler("broadcast", broadcast_start, filters=private_filter)],
        states={
            WAITING_BROADCAST: [MessageHandler(filters.ALL & ~filters.COMMAND & private_filter, broadcast_send)]
        },
        fallbacks=[CommandHandler("cancel", cancel, filters=private_filter)]
    )
    bot_application.add_handler(broadcast_conv)

    ref_conv = ConversationHandler(
        entry_points=[CommandHandler("createref", createref_start, filters=private_filter)],
        states={
            WAITING_REF_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND & private_filter, createref_get_name)]
        },
        fallbacks=[CommandHandler("cancel", cancel, filters=private_filter)]
    )
    bot_application.add_handler(ref_conv)

    ad_conv = ConversationHandler(
        entry_points=[CommandHandler("setad", setad_start, filters=private_filter)],
        states={
            WAITING_AD_CONTENT: [MessageHandler(filters.ALL & ~filters.COMMAND & private_filter, setad_get_content)]
        },
        fallbacks=[CommandHandler("cancel", cancel, filters=private_filter)]
    )
    bot_application.add_handler(ad_conv)

    bot_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & private_filter, handle_code))

    await bot_application.initialize()
    await bot_application.bot.set_webhook(WEBHOOK_URL)

    starlette_app = Starlette(debug=False, routes=[
        Route(WEBHOOK_PATH, webhook_handler, methods=["POST"]),
        Route("/healthcheck", healthcheck, methods=["GET"]),
    ])

    port = int(os.environ.get("PORT", 8080))
    print(f"✅ Bot ishga tushdi, webhook: {WEBHOOK_URL}")
    import uvicorn
    config = uvicorn.Config(starlette_app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())
