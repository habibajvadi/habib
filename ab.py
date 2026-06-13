import sqlite3
import threading
import time
import requests
import uuid
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
import os

# ---------- تنظیمات ----------
TOKEN = "8981742192:AAHC8z6u6GifXgMIafvzv0tn_Q2LV1mM2bQ"
BOT_USERNAME = "nevergivup_bot"

# ⚠️ این آدرس را به آدرس واقعی ربات در Railway/Render تغییر دهید
# مثال Railway: https://arnold.up.railway.app
# مثال Render: https://your-app.onrender.com
BASE_URL = "https://arnold.up.railway.app"   # ← این را عوض کن

# ---------- کانال الزامی ----------
REQUIRED_CHANNEL = "@film01385"

# زرین‌پال
ZP_MERCHANT_ID = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
ZP_REQUEST_URL = "https://api.zarinpal.com/pg/v4/payment/request.json"
ZP_VERIFY_URL = "https://api.zarinpal.com/pg/v4/payment/verify.json"
ZP_START_PAY = "https://www.zarinpal.com/pg/StartPay/"

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)   # تایپو رفع شد

# ---------- دیتابیس ----------
conn = sqlite3.connect("/tmp/tracker.db", check_same_thread=False)
c = conn.cursor()

c.execute("DROP TABLE IF EXISTS users")
c.execute("CREATE TABLE users (telegram_id INTEGER PRIMARY KEY, link_code TEXT UNIQUE)")

c.execute("""CREATE TABLE IF NOT EXISTS pending_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    link_code TEXT,
    owner_id INTEGER,
    clicker_id INTEGER,
    message_id INTEGER,
    expires_at DATETIME,
    cancelled BOOLEAN DEFAULT FALSE
)""")

c.execute("""CREATE TABLE IF NOT EXISTS subscriptions (
    user_id INTEGER PRIMARY KEY,
    expires_at DATETIME
)""")

c.execute("""CREATE TABLE IF NOT EXISTS pending_payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    authority TEXT UNIQUE,
    amount INTEGER,
    days INTEGER,
    created_at DATETIME
)""")

c.execute("""CREATE TABLE IF NOT EXISTS cancel_payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER,
    user_id INTEGER,
    authority TEXT UNIQUE,
    amount INTEGER,
    status TEXT DEFAULT 'pending',
    created_at DATETIME
)""")

c.execute("""CREATE TABLE IF NOT EXISTS user_photos (
    user_id INTEGER PRIMARY KEY,
    photo_id TEXT
)""")

c.execute("""CREATE TABLE IF NOT EXISTS user_texts (
    user_id INTEGER PRIMARY KEY,
    text TEXT
)""")

conn.commit()

# ---------- دیکشنری برای ذخیره موقت اطلاعات ارسال پیام ناشناس ----------
anonymous_temp = {}

# ========== بررسی عضویت در کانال ==========
def check_membership(user_id):
    try:
        member = bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        if member.status in ['member', 'administrator', 'creator']:
            return True
        return False
    except Exception as e:
        print(f"Error checking membership: {e}")
        return False

def require_channel(user_id):
    if check_membership(user_id):
        return True
    else:
        keyboard = InlineKeyboardMarkup()
        keyboard.add(InlineKeyboardButton("📢 عضویت در کانال", url=f"https://t.me/{REQUIRED_CHANNEL.replace('@', '')}"))
        keyboard.add(InlineKeyboardButton("✅ بررسی عضویت", callback_data="check_membership"))
        bot.send_message(
            user_id,
            f"❌ **دسترسی محدود شده**\n\n"
            f"برای استفاده از امکانات ربات، ابتدا باید در کانال زیر عضو شوید:\n\n"
            f"🔗 {REQUIRED_CHANNEL}\n\n"
            f"پس از عضویت، روی دکمه «بررسی عضویت» کلیک کنید.",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        return False

# ========== پنل اصلی با Reply Keyboard Markup ==========
def main_panel(user_id, message_id=None):
    keyboard = ReplyKeyboardMarkup(row_width=2, resize_keyboard=True, one_time_keyboard=False)
    btn_get_link = KeyboardButton("🔗 دریافت لینک من")
    btn_buy_subscription = KeyboardButton("💰 خرید اشتراک پرو")
    btn_buy_apple = KeyboardButton("🍎 خرید سیر")
    btn_set_photo = KeyboardButton("🖼 تنظیم عکس مچ گیری")
    btn_set_text = KeyboardButton("📝 تنظیم متن مچ گیری")
    btn_help = KeyboardButton("❓ راهنما")
    keyboard.add(btn_get_link)
    keyboard.add(btn_buy_subscription, btn_buy_apple)
    keyboard.add(btn_set_photo, btn_set_text)
    keyboard.add(btn_help)
    
    panel_text = (
        f"📱 **پنل کاربری**\n\n"
        f"👤 کاربر: {get_owner_name(user_id)}\n\n"
        f"❗️ **یک گزینه را انتخاب کنید...**"
    )
    if message_id:
        try:
            bot.edit_message_text(panel_text, user_id, message_id, parse_mode='Markdown')
            bot.send_message(user_id, "🔽 از دکمه‌های زیر استفاده کنید:", reply_markup=keyboard)
        except:
            bot.send_message(user_id, panel_text, reply_markup=keyboard, parse_mode='Markdown')
    else:
        bot.send_message(user_id, panel_text, reply_markup=keyboard, parse_mode='Markdown')

# ---------- توابع اشتراک و پرداخت ----------
def has_active_subscription(user_id):
    c.execute("SELECT expires_at FROM subscriptions WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    if row:
        expires_at = datetime.fromisoformat(row[0])
        if expires_at > datetime.now():
            return True
        else:
            c.execute("DELETE FROM subscriptions WHERE user_id = ?", (user_id,))
            conn.commit()
    return False

def add_subscription(user_id, days):
    current = datetime.now()
    c.execute("SELECT expires_at FROM subscriptions WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    if row:
        old_expires = datetime.fromisoformat(row[0])
        if old_expires > current:
            new_expires = old_expires + timedelta(days=days)
        else:
            new_expires = current + timedelta(days=days)
    else:
        new_expires = current + timedelta(days=days)
    c.execute("INSERT OR REPLACE INTO subscriptions (user_id, expires_at) VALUES (?, ?)",
              (user_id, new_expires.isoformat()))
    conn.commit()
    return new_expires

def get_subscription_info(user_id):
    c.execute("SELECT expires_at FROM subscriptions WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    if row:
        expires_at = datetime.fromisoformat(row[0])
        return expires_at
    return None

def create_payment_link(user_id, amount, days):
    authority = str(uuid.uuid4()).replace("-", "")[:20]
    callback_url = f"{BASE_URL}/verify?user_id={user_id}&days={days}"
    data = {
        "merchant_id": ZP_MERCHANT_ID,
        "amount": amount,
        "callback_url": callback_url,
        "description": f"خرید اشتراک {days} روزه ربات تله",
        "metadata": {"mobile": "", "email": ""}
    }
    try:
        response = requests.post(ZP_REQUEST_URL, json=data)
        result = response.json()
        if result.get("data", {}).get("code") == 100:
            authority = result["data"]["authority"]
            c.execute("INSERT INTO pending_payments (user_id, authority, amount, days, created_at) VALUES (?, ?, ?, ?, ?)",
                      (user_id, authority, amount, days, datetime.now().isoformat()))
            conn.commit()
            pay_link = f"{ZP_START_PAY}{authority}"
            return pay_link, None
        else:
            return None, "خطا در اتصال به درگاه پرداخت"
    except Exception as e:
        return None, str(e)

def verify_payment(authority, amount):
    data = {
        "merchant_id": ZP_MERCHANT_ID,
        "amount": amount,
        "authority": authority
    }
    try:
        response = requests.post(ZP_VERIFY_URL, json=data)
        result = response.json()
        if result.get("data", {}).get("code") == 100:
            return True, result["data"]["ref_id"]
        else:
            return False, result.get("errors", {}).get("code", "خطا")
    except Exception as e:
        return False, str(e)

# ---------- توابع اصلی ----------
def generate_link(telegram_id):
    code = str(telegram_id)
    c.execute("INSERT OR REPLACE INTO users (telegram_id, link_code) VALUES (?, ?)", (telegram_id, code))
    conn.commit()
    return f"https://t.me/{BOT_USERNAME}?start=track_{code}"

def get_owner_id_by_code(code):
    try:
        return int(code)
    except:
        return None

def get_owner_name(owner_id):
    try:
        chat = bot.get_chat(owner_id)
        first_name = chat.first_name or ""
        last_name = chat.last_name or ""
        return f"{first_name} {last_name}".strip()
    except:
        return "صاحب پروفایل"

def get_clicker_name(clicker_id):
    try:
        chat = bot.get_chat(clicker_id)
        first_name = chat.first_name or ""
        last_name = chat.last_name or ""
        return f"{first_name} {last_name}".strip()
    except:
        return "کاربر ناشناس"

def delete_message_later(chat_id, message_id, delay, clicker_id, owner_name, report_id):
    time.sleep(delay)
    c.execute("SELECT status FROM cancel_payments WHERE report_id = ? AND status = 'paid'", (report_id,))
    paid = c.fetchone()
    if paid:
        return
    try:
        bot.delete_message(chat_id, message_id)
    except:
        pass
    c.execute("SELECT cancelled FROM pending_reports WHERE id = ?", (report_id,))
    result = c.fetchone()
    if result and result[0] == False:
        c.execute("SELECT owner_id, clicker_id FROM pending_reports WHERE id = ?", (report_id,))
        row = c.fetchone()
        if row:
            owner_id, clicker_id = row
            clicker_name = get_clicker_name(clicker_id)
            keyboard = InlineKeyboardMarkup(row_width=2)
            keyboard.add(
                InlineKeyboardButton("💬 پیام ناشناس", callback_data=f"anon_{clicker_id}_{owner_id}"),
                InlineKeyboardButton("📝 بیوگرافی", callback_data=f"bio_{clicker_id}_{owner_id}"),
                InlineKeyboardButton("📨 پیوی", callback_data=f"pv_{clicker_id}_{owner_id}"),
                InlineKeyboardButton("🖼 عکس پروفایل", callback_data=f"photo_{clicker_id}_{owner_id}")
            )
            report_msg = f"🎯 **یک فضول در تله افتاد!**\n\n👤 نام: {clicker_name}\n⏰ زمان: {datetime.now().strftime('%H:%M:%S')}"
            try:
                bot.send_message(owner_id, report_msg, parse_mode='Markdown', reply_markup=keyboard)
            except:
                pass
            
            final_message = (
                f"⏰ **زمان شما تمام شد!**\n\n"
                f"گزارش فضولی شما به {owner_name} ارسال گردید.\n\n"
                f"❗️ **از پنل زیر استفاده کنید:**"
            )
            try:
                bot.send_message(clicker_id, final_message, parse_mode='Markdown')
                main_panel(clicker_id)
            except:
                pass

# ---------- هندلر استارت ----------
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    text = message.text
    if text.startswith("/start track_"):
        code = text.split("track_")[1]
        owner_id = get_owner_id_by_code(code)
        clicker_id = user_id
        if owner_id and owner_id != clicker_id:
            owner_name = get_owner_name(owner_id)
            keyboard = InlineKeyboardMarkup()
            keyboard.add(InlineKeyboardButton("❌ عدم ارسال گزارش فضولی", callback_data=f"cancel_{code}_{clicker_id}"))
            trap_message = (
                f"⚠️ **نباید این فضولی رو میکردی!**\n\n"
                f"الان این فضولیت برای {owner_name} ارسال شد، بهتره قبل از اینکه بیاد ببینه، "
                f"خودت بهش بگی داشتی فضولی میکردی 😊\n\n"
                f"برای عدم ارسال دکمه زیر را فشار دهید (فرصت شما 1 دقیقه و 15 ثانیه)"
            )
            msg = bot.send_message(clicker_id, trap_message, reply_markup=keyboard, parse_mode='Markdown')
            c.execute("INSERT INTO pending_reports (link_code, owner_id, clicker_id, message_id, expires_at) VALUES (?, ?, ?, ?, ?)",
                      (code, owner_id, clicker_id, msg.message_id, datetime.now() + timedelta(seconds=75)))
            conn.commit()
            report_id = c.lastrowid
            threading.Thread(target=delete_message_later, args=(clicker_id, msg.message_id, 75, clicker_id, owner_name, report_id)).start()
        elif owner_id == clicker_id:
            bot.send_message(clicker_id, "⚠️ این لینک مال خودته!")
            main_panel(clicker_id)
        else:
            bot.send_message(clicker_id, "❌ لینک نامعتبر!")
            main_panel(clicker_id)
    else:
        main_panel(user_id)

# ---------- دریافت لینک من ----------
@bot.message_handler(func=lambda message: message.text == "🔗 دریافت لینک من")
def handle_get_my_link(message):
    user_id = message.from_user.id
    if not require_channel(user_id):
        return
    link = generate_link(user_id)
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("📋 کپی لینک", callback_data=f"copy_link_{link}"))
    keyboard.add(InlineKeyboardButton("🔙 بازگشت به پنل", callback_data="back_to_panel"))
    bot.send_message(
        user_id,
        f"🔗 **لینک اختصاصی شما:**\n\n`{link}`\n\n"
        f"✅ این لینک مخصوص شماست.\n"
        f"📌 آن را در بیوگرافی یا کانال خود قرار دهید.\n"
        f"⚠️ هر کسی روی این لینک کلیک کند، در تله می‌افتد!",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("copy_link_"))
def copy_link_callback(call):
    bot.answer_callback_query(call.id, "✅ لینک با موفقیت کپی شد! (روی لینک نگه دارید و کپی کنید)", show_alert=True)

# ---------- دکمه‌های پنل ----------
@bot.message_handler(func=lambda message: message.text == "💰 خرید اشتراک پرو")
def handle_buy_subscription(message):
    user_id = message.from_user.id
    if not require_channel(user_id):
        return
    info = get_subscription_info(user_id)
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton("💰 اشتراک ۱ ماهه - ۱۰,۰۰۰ تومان", callback_data="pay_30_10000"),
        InlineKeyboardButton("💰 اشتراک ۳ ماهه - ۲۵,۰۰۰ تومان", callback_data="pay_90_25000"),
        InlineKeyboardButton("💰 اشتراک ۶ ماهه - ۴۵,۰۰۰ تومان", callback_data="pay_180_45000"),
        InlineKeyboardButton("🔙 بازگشت به پنل", callback_data="back_to_panel")
    )
    if info:
        days_left = (info - datetime.now()).days
        status = f"✅ اشتراک فعال تا {info.strftime('%Y/%m/%d')} ({days_left} روز باقی مونده)"
    else:
        status = "❌ اشتراک فعالی ندارید"
    bot.send_message(
        user_id,
        f"💳 **خرید اشتراک**\n\n{status}\n\n"
        f"یکی از گزینه‌های زیر را انتخاب کنید:",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )

@bot.message_handler(func=lambda message: message.text == "🍎 خرید سیر")
def handle_buy_apple(message):
    user_id = message.from_user.id
    if not require_channel(user_id):
        return
    hide_keyboard = ReplyKeyboardRemove()
    bot.send_message(
        user_id,
        "🍎 **خرید سیر**\n\nاین قابلیت به زودی اضافه می‌شود.\nبرای بازگشت به پنل، روی /start کلیک کنید.",
        reply_markup=hide_keyboard,
        parse_mode='Markdown'
    )
    threading.Timer(2, lambda: main_panel(user_id)).start()

@bot.message_handler(func=lambda message: message.text == "🖼 تنظیم عکس مچ گیری")
def handle_set_photo(message):
    user_id = message.from_user.id
    if not require_channel(user_id):
        return
    hide_keyboard = ReplyKeyboardRemove()
    bot.send_message(
        user_id,
        "🖼 **تنظیم عکس مچ گیری**\n\nلطفاً عکس مورد نظر خود را ارسال کنید:",
        reply_markup=hide_keyboard,
        parse_mode='Markdown'
    )
    bot.register_next_step_handler(message, save_photo)

def save_photo(message):
    user_id = message.from_user.id
    if message.photo:
        file_id = message.photo[-1].file_id
        c.execute("INSERT OR REPLACE INTO user_photos (user_id, photo_id) VALUES (?, ?)", (user_id, file_id))
        conn.commit()
        bot.send_message(user_id, "✅ عکس شما با موفقیت ذخیره شد!")
    else:
        bot.send_message(user_id, "❌ لطفاً یک عکس معتبر ارسال کنید.")
    main_panel(user_id)

@bot.message_handler(func=lambda message: message.text == "📝 تنظیم متن مچ گیری")
def handle_set_text(message):
    user_id = message.from_user.id
    if not require_channel(user_id):
        return
    hide_keyboard = ReplyKeyboardRemove()
    bot.send_message(
        user_id,
        "📝 **تنظیم متن مچ گیری**\n\nلطفاً متن مورد نظر خود را ارسال کنید:",
        reply_markup=hide_keyboard,
        parse_mode='Markdown'
    )
    bot.register_next_step_handler(message, save_text)

def save_text(message):
    user_id = message.from_user.id
    text = message.text
    c.execute("INSERT OR REPLACE INTO user_texts (user_id, text) VALUES (?, ?)", (user_id, text))
    conn.commit()
    bot.send_message(user_id, f"✅ متن شما با موفقیت ذخیره شد!\n\nمتن شما:\n{text}")
    main_panel(user_id)

@bot.message_handler(func=lambda message: message.text == "❓ راهنما")
def handle_help(message):
    user_id = message.from_user.id
    if not require_channel(user_id):
        return
    help_text = (
        f"📚 **راهنمای جامع استفاده از ربات**\n\n"
        f"با سلام و احترام. به بخش راهنمای ربات خوش آمدید. در این بخش با تمامی امکانات و نحوه عملکرد دقیق ربات آشنا خواهید شد:\n\n"
        f"**۱. نحوه کارکرد ربات (سیستم مچ‌گیری):**\n"
        f"شما می‌توانید با دریافت لینک اختصاصی خود از طریق ربات و قرار دادن آن در بخش بیوگرافی (Bio) حساب کاربری‌تان، متوجه شوید چه کسانی در حال بازدید از پروفایل شما هستند.\n"
        f"🔹 *نکته:* امکان قرار دادن این لینک به صورت مخفی (Hyperlink) در بیوگرافی وجود دارد.\n"
        f"به محض اینکه شخصی از روی کنجکاوی روی لینک شما کلیک کرده و وارد ربات شود، ربات فوراً پیامی با مضمون «یک فضول در تله افتاد!» برای شما ارسال می‌کند. این گزارش شامل اطلاعات کامل شخص است:\n"
        f"▫️ نام کاربر\n"
        f"▫️ آیدی (لینک ورود به پیوی)\n"
        f"▫️ عکس پروفایل\n"
        f"▫️ بیوگرافی (در صورت وجود)\n\n"
        f"**۲. اشتراک ویژه (پرو - ۳۰ روزه):**\n"
        f"با تهیه اشتراک پرو، امکانات پیشرفته زیر در اختیار شما قرار می‌گیرد:\n"
        f"🔹 **ارسال پیام ناشناس:** می‌توانید از طریق ربات، برای شخصی که در تله شما افتاده است به صورت کاملاً ناشناس پیام ارسال کنید.\n"
        f"🔹 **مشاهده پروفایل افراد بدون آیدی:** اگر شخصی که در تله افتاده آیدی عمومی (Username) نداشته باشد، با اشتراک پرو همچنان می‌توانید عکس پروفایل و بیوگرافی او را مشاهده کنید.\n\n"
        f"**۳. شخصی‌سازی تله (متن و عکس مچ‌گیری):**\n"
        f"شما می‌توانید واکنش ربات به فردی که در تله می‌افتد را کاملاً شخصی‌سازی کنید:\n"
        f"🔹 **تنظیم متن مچ‌گیری:** پیامی که فرد به محض کلیک روی لینک شما دریافت می‌کند را تغییر دهید.\n"
        f"🔹 **تنظیم عکس مچ‌گیری:** علاوه بر متن، می‌توانید یک تصویر دلخواه تنظیم کنید تا به محض ورود شخص، آن عکس نیز برای وی ارسال شود.\n\n"
        f"**۴. اشتراک سپر (محافظت و مچ‌گیری آنی):**\n"
        f"داشتن اشتراک سپر، امنیت و سرعت شما را به حداکثر می‌رساند:\n"
        f"🔹 **محافظت از شما:** اگر خودتان روی لینک شخص دیگری کلیک کنید و در تله بیفتید، گزارش ورود شما کاملاً مسدود شده و برای طرف مقابل ارسال نخواهد شد.\n"
        f"🔹 **گزارش آنی و قطعی:** به محض اینکه شخصی در تله شما بیفتد، گزارش آن بدون هیچ وقفه‌ای و به صورت آنی برای شما ارسال می‌شود و نیاز به پرداخت موردی برای دیدن شکار از بین می‌رود.\n\n"
        f"💬 در صورت بروز هرگونه مشکل یا داشتن سوالات بیشتر، با @Asd00120A در ارتباط باشید."
    )
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("🔙 بازگشت به پنل", callback_data="back_to_panel"))
    hide_keyboard = ReplyKeyboardRemove()
    bot.send_message(user_id, help_text, reply_markup=keyboard, parse_mode='Markdown')

# ========== پیام ناشناس رایگان ==========
@bot.callback_query_handler(func=lambda call: call.data.startswith("anon_"))
def anonymous_message(call):
    _, clicker_id, owner_id = call.data.split("_")
    clicker_id = int(clicker_id)
    owner_id = int(owner_id)
    user_id = call.from_user.id
    
    if user_id != owner_id:
        bot.answer_callback_query(call.id, "این دکمه فقط برای صاحب لینک قابل استفاده است!", show_alert=True)
        return
    
    anonymous_temp[user_id] = clicker_id
    
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except:
        pass
    
    cancel_keyboard = InlineKeyboardMarkup()
    cancel_keyboard.add(InlineKeyboardButton("❌ انصراف", callback_data="cancel_anonymous"))
    
    bot.send_message(
        user_id,
        "💬 **ارسال پیام ناشناس**\n\n"
        "لطفاً متن پیام خود را ارسال کنید.\n"
        "این پیام **به صورت ناشناس** برای کاربر فضول فرستاده خواهد شد.\n\n"
        "⚠️ توجه: نام و اطلاعات شما فاش نمی‌شود.",
        reply_markup=cancel_keyboard,
        parse_mode='Markdown'
    )
    bot.register_next_step_handler_by_chat_id(user_id, receive_anonymous_message, clicker_id, user_id)
    bot.answer_callback_query(call.id)

def receive_anonymous_message(message, clicker_id, owner_id):
    user_id = message.from_user.id
    if user_id not in anonymous_temp:
        return
    
    if message.text:
        anonymous_text = message.text
        try:
            bot.send_message(
                clicker_id,
                f"💌 **پیام ناشناس**\n\n"
                f"یک کاربر ناشناس به شما پیام داده است:\n\n"
                f"「 {anonymous_text} 」\n\n"
                f"🔹 شما نمی‌توانید پاسخ دهید.",
                parse_mode='Markdown'
            )
            bot.send_message(
                user_id,
                "✅ **پیام شما با موفقیت ارسال شد!**\n\nپیام شما به صورت ناشناس برای کاربر فضول فرستاده شد.",
                parse_mode='Markdown'
            )
        except Exception as e:
            bot.send_message(
                user_id,
                f"❌ **خطا در ارسال پیام**\n\nکاربر فضول ممکن است ربات را بلاک کرده باشد یا مشکل دیگری وجود دارد.\n\nخطا: {e}",
                parse_mode='Markdown'
            )
    else:
        bot.send_message(user_id, "❌ لطفاً فقط متن ارسال کنید. پیام ناشناس ارسال نشد.", parse_mode='Markdown')
    
    anonymous_temp.pop(user_id, None)
    main_panel(user_id)

@bot.callback_query_handler(func=lambda call: call.data == "cancel_anonymous")
def cancel_anonymous(call):
    user_id = call.from_user.id
    anonymous_temp.pop(user_id, None)
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        pass
    bot.send_message(user_id, "❌ عملیات ارسال پیام ناشناس لغو شد.", parse_mode='Markdown')
    main_panel(user_id)

# ========== دکمه‌های تله و پرداخت ==========
@bot.callback_query_handler(func=lambda call: call.data.startswith("cancel_"))
def cancel_report_payment_page(call):
    _, code, clicker_id = call.data.split("_")
    clicker_id = int(clicker_id)
    if call.from_user.id != clicker_id:
        bot.answer_callback_query(call.id, "این دکمه مال تو نیست!", show_alert=True)
        return
    c.execute("SELECT id FROM pending_reports WHERE link_code = ? AND clicker_id = ? AND cancelled = FALSE ORDER BY id DESC LIMIT 1", 
              (code, clicker_id))
    row = c.fetchone()
    if not row:
        bot.answer_callback_query(call.id, "گزارشی یافت نشد!", show_alert=True)
        return
    report_id = row[0]
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        pass
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton("📄 مشاهده جزئیات", callback_data=f"details_{report_id}"),
        InlineKeyboardButton("💳 پرداخت", callback_data=f"fake_pay_{report_id}")
    )
    payment_text = (
        f"💳 **درخواست پول**\n\n"
        f"**لغو ارسال گزارش فضولی**\n"
        f"با پرداخت فقط ۶,۵۰۰ تومان، گزارش فضولی شما برای صاحب لینک ارسال نخواهد شد.\n\n"
        f"لغو گزارش: 65000\n"
        f"مبلغ: ۶۵,۰۰۰ ریال"
    )
    bot.send_message(call.message.chat.id, payment_text, reply_markup=keyboard, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data.startswith("fake_pay_"))
def fake_payment(call):
    _, report_id = call.data.split("_")
    report_id = int(report_id)
    c.execute("UPDATE pending_reports SET cancelled = TRUE WHERE id = ?", (report_id,))
    conn.commit()
    bot.answer_callback_query(call.id, "✅ پرداخت با موفقیت انجام شد!")
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        pass
    bot.send_message(
        call.message.chat.id,
        "✅ **پرداخت شما با موفقیت تایید شد!**\n\n"
        "گزارش فضولی شما لغو گردید و برای صاحب لینک ارسال نخواهد شد.\n\n"
        "🙏 از شما متشکریم.",
        parse_mode='Markdown'
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("details_"))
def show_details(call):
    _, report_id = call.data.split("_")
    bot.answer_callback_query(call.id)
    details_msg = (
        f"📋 **جزئیات پرداخت**\n\n"
        f"💰 مبلغ: ۶,۵۰۰ تومان (۶۵,۰۰۰ ریال)\n"
        f"📝 دلیل: لغو ارسال گزارش فضولی\n"
        f"⏱ زمان باقی مانده: کمتر از ۷۵ ثانیه\n\n"
        f"پس از پرداخت موفق، گزارش شما ارسال نخواهد شد."
    )
    bot.send_message(call.message.chat.id, details_msg, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data == "check_membership")
def check_membership_callback(call):
    user_id = call.from_user.id
    if check_membership(user_id):
        bot.answer_callback_query(call.id, "✅ عضویت شما تأیید شد! حالا می‌توانید از ربات استفاده کنید.")
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        main_panel(user_id)
    else:
        bot.answer_callback_query(call.id, "❌ شما هنوز عضو کانال نشده‌اید!", show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data == "back_to_panel")
def back_to_panel_inline(call):
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except:
        pass
    main_panel(call.from_user.id)
    bot.answer_callback_query(call.id)

# ========== ۴ دکمه دیگر (بیوگرافی، پیوی، عکس) با بررسی اشتراک ==========
def check_subscription_and_forward(call, feature_name):
    user_id = call.from_user.id
    if not has_active_subscription(user_id):
        keyboard = InlineKeyboardMarkup()
        keyboard.add(InlineKeyboardButton("💰 خرید اشتراک", callback_data="buy_subscription"))
        bot.send_message(
            user_id,
            f"❌ **دسترسی غیرفعال**\n\nشما برای استفاده از قابلیت «{feature_name}» باید اشتراک تهیه کنید.\n\n"
            f"برای خرید اشتراک دکمه زیر رو بزن 👇",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        bot.answer_callback_query(call.id, "ابتدا اشتراک بخرید!")
        return False
    return True

@bot.callback_query_handler(func=lambda call: call.data.startswith("bio_"))
def show_bio(call):
    if not check_subscription_and_forward(call, "مشاهده بیوگرافی"):
        return
    _, clicker_id, owner_id = call.data.split("_")
    bot.answer_callback_query(call.id)
    try:
        chat = bot.get_chat(int(clicker_id))
        bio = getattr(chat, 'bio', None)
        if bio:
            bot.send_message(call.message.chat.id, f"📝 **بیوگرافی کاربر:**\n\n{bio}", parse_mode='Markdown')
        else:
            bot.send_message(call.message.chat.id, "❌ این کاربر بیوگرافی تنظیم نکرده است.")
    except:
        bot.send_message(call.message.chat.id, "❌ امکان نمایش بیوگرافی وجود ندارد.")

@bot.callback_query_handler(func=lambda call: call.data.startswith("pv_"))
def send_pv(call):
    if not check_subscription_and_forward(call, "ارسال پیوی"):
        return
    _, clicker_id, owner_id = call.data.split("_")
    bot.answer_callback_query(call.id)
    try:
        chat = bot.get_chat(int(clicker_id))
        username = chat.username
        if username:
            link = f"https://t.me/{username}"
        else:
            link = f"https://t.me/{clicker_id}"
        bot.send_message(call.message.chat.id, f"🔗 لینک پیوی:\n`{link}`", parse_mode='Markdown')
    except:
        bot.send_message(call.message.chat.id, "❌ امکان ساخت لینک پیوی وجود ندارد.")

@bot.callback_query_handler(func=lambda call: call.data.startswith("photo_"))
def show_photo(call):
    if not check_subscription_and_forward(call, "مشاهده عکس پروفایل"):
        return
    _, clicker_id, owner_id = call.data.split("_")
    bot.answer_callback_query(call.id)
    try:
        photos = bot.get_user_profile_photos(int(clicker_id), limit=1)
        if photos.total_count > 0:
            file_id = photos.photos[0][-1].file_id
            bot.send_photo(call.message.chat.id, file_id, caption=f"🖼 عکس پروفایل کاربر")
        else:
            bot.send_message(call.message.chat.id, "❌ عکس پروفایل ندارد")
    except:
        bot.send_message(call.message.chat.id, "❌ امکان نمایش عکس وجود ندارد.")

# ========== خرید اشتراک (زرین‌پال) ==========
@bot.callback_query_handler(func=lambda call: call.data.startswith("pay_"))
def handle_payment(call):
    _, days, amount = call.data.split("_")
    days = int(days)
    amount = int(amount)
    user_id = call.from_user.id
    bot.answer_callback_query(call.id, "در حال ساخت لینک پرداخت...")
    pay_link, error = create_payment_link(user_id, amount, days)
    if pay_link:
        keyboard = InlineKeyboardMarkup()
        keyboard.add(InlineKeyboardButton("💳 پرداخت آنلاین", url=pay_link))
        keyboard.add(InlineKeyboardButton("🔙 بازگشت به پنل", callback_data="back_to_panel"))
        bot.send_message(
            user_id,
            f"✅ لینک پرداخت ساخته شد.\n\n"
            f"💰 مبلغ: {amount:,} تومان\n"
            f"📅 مدت: {days} روز\n\n"
            f"🔗 روی دکمه زیر بزن تا به درگاه پرداخت بری.\n"
            f"بعد از پرداخت، اشتراکت خودکار فعال میشه.",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
    else:
        bot.send_message(user_id, f"❌ خطا در ساخت لینک پرداخت: {error}\nلطفاً بعداً تلاش کن.")

@app.route('/verify', methods=['GET'])
def verify_payment_route():
    user_id = request.args.get('user_id')
    days = request.args.get('days')
    authority = request.args.get('Authority')
    status = request.args.get('Status')
    if not user_id or not days or not authority:
        return "پارامترهای ناقص", 400
    user_id = int(user_id)
    days = int(days)
    if status != "OK":
        return "پرداخت ناموفق یا توسط کاربر لغو شده است", 400
    c.execute("SELECT amount FROM pending_payments WHERE authority = ? AND user_id = ?", (authority, user_id))
    row = c.fetchone()
    if not row:
        return "تراکنش یافت نشد", 404
    amount = row[0]
    success, ref_id = verify_payment(authority, amount)
    if success:
        new_expires = add_subscription(user_id, days)
        c.execute("DELETE FROM pending_payments WHERE authority = ?", (authority,))
        conn.commit()
        try:
            bot.send_message(user_id, f"✅ **پرداخت شما با موفقیت تایید شد!**\n\n🎉 اشتراک {days} روزه شما فعال شد.\n📅 اعتبار تا {new_expires.strftime('%Y/%m/%d')}", parse_mode='Markdown')
            main_panel(user_id)
        except:
            pass
        return f"پرداخت با موفقیت تایید شد. کد رهگیری: {ref_id}", 200
    else:
        return f"پرداخت تایید نشد. کد خطا: {ref_id}", 400

@app.route('/verify_cancel', methods=['GET'])
def verify_cancel_payment():
    user_id = request.args.get('user_id')
    report_id = request.args.get('report_id')
    authority = request.args.get('Authority')
    status = request.args.get('Status')
    if not user_id or not report_id or not authority:
        return "پارامترهای ناقص", 400
    user_id = int(user_id)
    report_id = int(report_id)
    if status != "OK":
        return "پرداخت ناموفق یا توسط کاربر لغو شده است", 400
    c.execute("SELECT amount FROM cancel_payments WHERE authority = ? AND user_id = ? AND report_id = ?", 
              (authority, user_id, report_id))
    row = c.fetchone()
    if not row:
        return "تراکنش یافت نشد", 404
    amount = row[0]
    data = {"merchant_id": ZP_MERCHANT_ID, "amount": amount, "authority": authority}
    try:
        response = requests.post(ZP_VERIFY_URL, json=data)
        result = response.json()
        if result.get("data", {}).get("code") == 100:
            c.execute("UPDATE cancel_payments SET status = 'paid' WHERE authority = ?", (authority,))
            c.execute("UPDATE pending_reports SET cancelled = TRUE WHERE id = ?", (report_id,))
            conn.commit()
            try:
                bot.send_message(user_id, "✅ **پرداخت شما با موفقیت تایید شد!**\n\nگزارش فضولی شما لغو گردید.", parse_mode='Markdown')
            except:
                pass
            return f"✅ پرداخت موفق. کد رهگیری: {result['data']['ref_id']}", 200
        else:
            return f"❌ پرداخت تایید نشد. کد خطا: {result.get('errors', {}).get('code', 'unknown')}", 400
    except Exception as e:
        return f"خطا: {e}", 500

# ---------- مسیرهای Flask ----------
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        update = telebot.types.Update.de_json(request.get_data().decode('UTF-8'))
        bot.process_new_updates([update])
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"status": "error"}), 500

@app.route('/')
def index():
    return "ربات آنلاین است", 200

def set_webhook():
    # حذف webhook قبلی و تنظیم مجدد
    bot.remove_webhook()
    time.sleep(1)
    webhook_url = f"{BASE_URL}/webhook"
    result = bot.set_webhook(url=webhook_url)
    if result:
        print(f"✅ Webhook set successfully to {webhook_url}")
    else:
        print(f"❌ Failed to set webhook to {webhook_url}")

# ---------- نقطه ورود اصلی ----------
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    
    # تنظیم webhook (آدرس BASE_URL باید درست باشد)
    set_webhook()
    
    # اجرای سرور Flask
    print(f"🚀 Starting Flask server on port {port}...")
    app.run(host='0.0.0.0', port=port)