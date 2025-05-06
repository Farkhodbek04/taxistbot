import os
import json
import logging
import asyncio
import re
from datetime import datetime
from dotenv import load_dotenv
from rapidfuzz import fuzz
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
import httpx

# Ensure log directory exists
LOG_DIR = "/app/logs"
LOG_FILE = os.path.join(LOG_DIR, "taxi_bot.log")
os.makedirs(LOG_DIR, exist_ok=True)

# Custom handler for taxi_bot.log to capture only __main__ ERROR logs
class MainErrorOnlyHandler(logging.FileHandler):
    def emit(self, record):
        if record.name == "__main__" and record.levelno == logging.ERROR:
            super().emit(record)

# Logging sozlamalari
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.DEBUG,  # Capture all levels for console
    handlers=[
        logging.StreamHandler(),  # Console output for all levels
        MainErrorOnlyHandler(LOG_FILE)  # File output for __main__ ERROR only
    ]
)
logger = logging.getLogger(__name__)

# Suppress verbose library logs
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

# Atrof-muhit o'zgaruvchilarini yuklash
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
MAIN_GROUP_IDS = os.getenv("MAIN_GROUP_IDS", "").split(",")
MAIN_GROUP_LINKS = os.getenv("MAIN_GROUP_LINKS", "").split(",")
DRIVER_GROUP_IDS = os.getenv("DRIVER_GROUP_IDS", "").split(",")
SUPERADMIN = os.getenv("SUPERADMIN")  # Super admin ID for error notifications

# Sozlamalar fayli
CONFIG_FILE = "config.json"

# Telefon raqamini aniqlash uchun regex
PHONE_REGEX = r"(\+998\d{9}|998\d{9}|\+?\d{9,12})"

# Super admin ga xato xabarini yuborish with retries
async def notify_superadmin(context: ContextTypes.DEFAULT_TYPE, error_message: str, retries=3, delay=5):
    if not SUPERADMIN:
        logger.warning("SUPERADMIN .env faylida aniqlanmadi, xato xabari yuborilmadi.")
        return
    for attempt in range(retries):
        try:
            await context.bot.send_message(
                chat_id=SUPERADMIN,
                text=f"🚨 Botda xato yuz berdi:\n{error_message}"
            )
            logger.info(f"Super admin ({SUPERADMIN}) ga xato xabari yuborildi.")
            return
        except Exception as e:
            logger.error(f"Super admin ga xabar yuborishda xato (urinish {attempt + 1}/{retries}): {e}")
            if attempt < retries - 1:
                await asyncio.sleep(delay)
    logger.error(f"Super admin ({SUPERADMIN}) ga xabar yuborish muvaffaqiyatsiz yakunlandi.")

# Sozlamalarni boshlash
def init_config():
    if not os.path.exists(CONFIG_FILE):
        default_config = {
            "keywords": ["taksi", "dan", "ga"],
            "admins": []
        }
        with open(CONFIG_FILE, "w") as f:
            json.dump(default_config, f, indent=4)

# Sozlamalarni yuklash
def load_config():
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)

# Sozlamalarni saqlash
def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)

# Admin tekshiruvi
def is_admin(user_id, config):
    return user_id in config["admins"]

# /start buyrug'i
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Taksi Bot ishga tushdi! Bu bot asosiy guruhlardagi taksi so'rovlarini kuzatib, haydovchilarga yuboradi. "
        "\n\nAdminlar /kalitqosh, /kalitochir, /kalitlar buyrug'lari orqali kalit so'zlarni boshqarishi mumkin. "
        "\n\nTelefon raqamingizni yuborish uchun /sharecontact buyrug'idan foydalaning."
    )

# /sharecontact buyrug'i
async def share_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Iltimos, telefon raqamingizni bot bilan ulashish uchun 'Kontakt yuborish' tugmasini bosing yoki "
        "xabaringizda telefon raqamingizni kiriting (masalan, +998901234567)."
    )

# Kalit so'z qo'shish
async def add_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    config = load_config()
    
    if not is_admin(user_id, config):
        await update.message.reply_text("Sizda bu buyruqni ishlatish huquqi yo'q.")
        return
    
    if not context.args:
        await update.message.reply_text("Iltimos, kalit so'z kiriting. Foydalanish: /kalitqosh <kalit_soz>")
        return
    
    keyword = " ".join(context.args).lower()
    if keyword in config["keywords"]:
        await update.message.reply_text(f"'{keyword}' kalit so'zi allaqachon mavjud.")
        return
    
    config["keywords"].append(keyword)
    save_config(config)
    await update.message.reply_text(f"'{keyword}' kalit so'zi qo'shildi.")

# Kalit so'zni o'chirish
async def remove_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    config = load_config()
    
    if not is_admin(user_id, config):
        await update.message.reply_text("Sizda bu buyruqni ishlatish huquqi yo'q.")
        return
    
    if not context.args:
        await update.message.reply_text("Iltimos, kalit so'z kiriting. Foydalanish: /kalitochir <kalit_soz>")
        return
    
    keyword = " ".join(context.args).lower()
    if keyword not in config["keywords"]:
        await update.message.reply_text(f"'{keyword}' kalit so'zi topilmadi.")
        return
    
    config["keywords"].remove(keyword)
    save_config(config)
    await update.message.reply_text(f"'{keyword}' kalit so'zi o'chirildi.")

# Kalit so'zlarni ro'yxatlash
async def list_keys(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    config = load_config()
    
    if not is_admin(user_id, config):
        await update.message.reply_text("Sizda bu buyruqni ishlatish huquqi yo'q.")
        return
    
    keywords = config["keywords"]
    if not keywords:
        await update.message.reply_text("Kalit so'zlar mavjud emas.")
        return
    
    await update.message.reply_text("Joriy kalit so'zlar:\n" + "\n".join(keywords))

# Fuzzy kalit so'z mosligini tekshirish
def has_fuzzy_match(text, keyword, threshold=95):
    # Check if text is at least as long as keyword to prevent partial matches
    if len(text) < len(keyword):
        logger.debug(f"No match: '{text}' too short for '{keyword}'")
        return False
    score = fuzz.partial_ratio(text.lower(), keyword.lower())
    logger.debug(f"Moslik skor: '{text}' vs '{keyword}' = {score}")
    return score >= threshold

def count_matched_keywords(message, keywords):
    match_count = 0
    matched_keywords = []
    words = message.split()
    
    for keyword in keywords:
        # Agar kalit so'z bir so'zdan iborat bo'lsa, har bir so'zni tekshirish
        if len(keyword.split()) == 1:
            for word in words:
                if has_fuzzy_match(word, keyword, threshold=95):
                    match_count += 1
                    matched_keywords.append(f"'{word}' ~ '{keyword}'")
                    logger.debug(f"So'z mosligi topildi: '{word}' ~ '{keyword}'")
                    break
        # Agar kalit so'z ibora bo'lsa, butun xabarni tekshirish
        else:
            if has_fuzzy_match(message, keyword, threshold=70):
                match_count += 1
                matched_keywords.append(f"'{message}' ~ '{keyword}'")
                logger.debug(f"Ibora mosligi topildi: '{message}' ~ '{keyword}'")
    
    if match_count > 0:
        logger.info(f"Mos kelgan kalit so'zlar: {', '.join(matched_keywords)}")
    else:
        logger.info(f"Hech qanday kalit so'z mos kelmadi: '{message}'")
    return match_count

# Telefon raqamini xabardan izlash
def extract_phone_number(text):
    match = re.search(PHONE_REGEX, text)
    return match.group(0) if match else None

# Kontakt xabarlarini qayta ishlash
async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.contact:
        return

    chat_id_str = str(update.effective_chat.id)
    if chat_id_str not in [str(gid).strip() for gid in MAIN_GROUP_IDS]:
        return

    contact = update.message.contact
    phone_number = contact.phone_number
    logger.info(f"Kontakt orqali telefon raqami qabul qilindi: {phone_number}")

    username = update.message.from_user.username
    user_id = update.message.from_user.id
    timestamp = datetime.fromtimestamp(update.message.date.timestamp()).strftime("%Y-%m-%d %H:%M:%S")
    group_name = update.effective_chat.title or "Asosiy Guruh"
    
    try:
        group_index = [str(gid).strip() for gid in MAIN_GROUP_IDS].index(chat_id_str)
        group_link = MAIN_GROUP_LINKS[group_index].strip()
    except (IndexError, ValueError):
        group_link = "https://t.me/sizningguruhingiz"
        logger.warning(f"Guruh ID {chat_id_str} uchun mos havola topilmadi.")

    message_link = f"{group_link}/{update.message.message_id}" if group_link else "Xabar havolasi mavjud emas"
    user_display = f"@{username}" if username else f"<a href='tg://user?id={user_id}'>Foydalanuvchi ID: {user_id}</a>"
    linked_group_name = f"<a href='{message_link}'>{group_name}</a>"

    formatted_message = (
        f"🚖 Yangi Taksi So'rovi (Kontakt)\n"
        f"👤 Kimdan: {user_display}\n"
        f"📞 Telefon: {phone_number}\n"
        f"🏢 Guruh: {linked_group_name}\n"
        f"🕒 Vaqt: {timestamp}\n"
        f"💬 Xabar: Kontakt yuborildi"
    )

    for driver_group_id in DRIVER_GROUP_IDS:
        try:
            await context.bot.send_message(
                chat_id=driver_group_id.strip(),
                text=formatted_message,
                parse_mode="HTML"
            )
            await asyncio.sleep(0.5)
        except Exception as e:
            error_msg = f"{driver_group_id} guruhiga xabar yuborishda xato: {e}"
            logger.error(error_msg)
            await notify_superadmin(context, error_msg)

# Asosiy guruhlardagi xabarlarni qayta ishlash
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    logger.debug(f"Xabar qabul qilindi: {update.message.text} guruhdan {update.effective_chat.id}")
    
    chat_id_str = str(update.effective_chat.id)
    if chat_id_str not in [str(gid).strip() for gid in MAIN_GROUP_IDS]:
        return

    message = update.message.text.lower()
    config = load_config()
    keywords = config["keywords"]

    matched_count = count_matched_keywords(message, keywords)
    logger.info(f"Mos kelgan kalit so'zlar soni: {matched_count}")

    if matched_count >= 1:
        logger.info(f"Mijoz xabari aniqlandi: {message}")
        username = update.message.from_user.username
        user_id = update.message.from_user.id
        phone_number = extract_phone_number(update.message.text) or "Noma'lum"
        timestamp = datetime.fromtimestamp(update.message.date.timestamp()).strftime("%Y-%m-%d %H:%M:%S")
        group_name = update.effective_chat.title or "Asosiy Guruh"
        
        try:
            group_index = [str(gid).strip() for gid in MAIN_GROUP_IDS].index(chat_id_str)
            group_link = MAIN_GROUP_LINKS[group_index].strip()
        except (IndexError, ValueError):
            group_link = "https://t.me/yourgroup"
            logger.warning(f"Guruh ID {chat_id_str} uchun mos havola topilmadi.")

        message_link = f"{group_link}/{update.message.message_id}" if group_link else "Xabar havolasi mavjud emas"
        user_display = f"@{username}" if username else f"<a href='tg://user?id={user_id}'>Foydalanuvchi ID: {user_id}</a>"
        linked_group_name = f"<a href='{message_link}'>{group_name}</a>"

        formatted_message = (
            f"🚖 Yangi Taksi So'rovi\n"
            f"👤 Kimdan: {user_display}\n"
            f"📞 Telefon: {phone_number}\n"
            f"🏢 Guruh: {linked_group_name}\n"
            f"🕒 Vaqt: {timestamp}\n"
            f"💬 Xabar: {update.message.text}"
        )
        
        for driver_group_id in DRIVER_GROUP_IDS:
            try:
                await context.bot.send_message(
                    chat_id=driver_group_id.strip(),
                    text=formatted_message,
                    parse_mode="HTML"
                )
                await asyncio.sleep(0.5)
            except Exception as e:
                error_msg = f"{driver_group_id} guruhiga xabar yuborishda xato: {e}"
                logger.error(error_msg)
                await notify_superadmin(context, error_msg)

# Xato ishlovchisi
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    error_msg = f"Yangi xabar {update} xatoga sabab bo'ldi {context.error}"
    logger.error(error_msg)
    await notify_superadmin(context, error_msg)

def main():
    if len(MAIN_GROUP_IDS) != len(MAIN_GROUP_LINKS):
        error_msg = "MAIN_GROUP_IDS va MAIN_GROUP_LINKS soni mos emas. Iltimos, .env faylini tekshiring."
        logger.error(error_msg)
        # Cannot notify superadmin yet as context is not available
        return

    init_config()
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("sharecontact", share_contact))
    application.add_handler(CommandHandler("kalitqosh", add_key))
    application.add_handler(CommandHandler("kalitochir", remove_key))
    application.add_handler(CommandHandler("kalitlar", list_keys))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    application.add_error_handler(error_handler)
    
    logger.info("Bot ishga tushmoqda...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()