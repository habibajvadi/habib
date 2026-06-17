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
TOKEN = "8814873551:AAG-SGCNsBoiVjWLRTBx83Buc-RYWt5MIOw"
BOT_USERNAME = "staystrongs_bot"
BASE_URL = "https://habib-q5vo.onrender.com"

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# ---------- دیتابیس ----------
conn = sqlite3.connect("/tmp/tracker.db", check_same_thread=False)
c = conn.cursor()

c.execute("DROP TABLE IF EXISTS users")
c.execute("CREATE TABLE users (telegram_id INTEGER PRIMARY KEY, link_code TEXT UNIQUE, user_name TEXT)")

c.execute("""CREATE TABLE IF NOT EXISTS pending_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    link_code TEXT,
    owner_id INTEGER,
    clicker_id INTEGER,
    message_id INTEGER,
    expires_at DATETIME,
    cancelled BOOLEAN DEFAULT FALSE
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

c.execute("""CREATE TABLE IF NOT EXISTS trapped_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER,
    clicker_id INTEGER,
    clicker_name TEXT,
    clicker_username TEXT,
    trapped_at TEXT
)""")

conn.commit()

anonymous_temp = {}

# ========== مدیران ربات ==========
ADMIN_IDS = [8521463103, 5333419558]

# ========== توابع کمکی برای آمار ==========
def get_total_users():
    c.execute("SELECT COUNT(*) FROM users")
    return c.fetchone()[0]

def get_total_reports():
    c.execute("SELECT COUNT(*) FROM pending_reports WHERE cancelled = 0")
    return c.fetchone()[0]

def get_total_trapped():
    c.execute("SELECT COUNT(*) FROM trapped_history")
    return c.fetchone()[0]

def get_total_photos():
    c.execute("SELECT COUNT(*) FROM user_photos")
    return c.fetchone()[0]

# ========== پنل اصلی ==========
def main_panel(user_id, message_id=None):
    keyboard = ReplyKeyboardMarkup(row_width=2, resize_keyboard=True, one_time_keyboard=False)
    btn_get_link = KeyboardButton("🔗 دریافت لینک من")
    btn_buy_apple = KeyboardButton("🍎 خرید سیر")
    btn_set_photo = KeyboardButton("🖼 تنظیم عکس مچ گیری")
    btn_del_photo = KeyboardButton("🗑 حذف عکس مچ گیری")
    btn_trapped_list = KeyboardButton("📋 کاربران در تله افتاده اخیر")
    btn_help = KeyboardButton("❓ راهنما")
    
    keyboard.add(btn_get_link)
    keyboard.add(btn_buy_apple)
    keyboard.add(btn_set_photo, btn_del_photo)
    keyboard.add(btn_trapped_list, btn_help)
    
    panel_text = f"📱 **پنل کاربری**\n\n👤 کاربر: {get_owner_name(user_id)}\n\n❗️ **یک گزینه را انتخاب کنید...**"
    if message_id:
        try:
            bot.edit_message_text(panel_text, user_id, message_id, parse_mode='Markdown')
            bot.send_message(user_id, "🔽 از دکمه‌های زیر استفاده کنید:", reply_markup=keyboard)
        except:
            bot.send_message(user_id, panel_text, reply_markup=keyboard, parse_mode='Markdown')
    else:
        bot.send_message(user_id, panel_text, reply_markup=keyboard, parse_mode='Markdown')

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
    c.execute("SELECT user_name FROM users WHERE telegram_id = ?", (owner_id,))
    row = c.fetchone()
    if row and row[0]:
        return row[0]
    try:
        chat = bot.get_chat(owner_id)
        name = f"{chat.first_name or ''} {chat.last_name or ''}".strip()
        if name:
            c.execute("UPDATE users SET user_name = ? WHERE telegram_id = ?", (name, owner_id))
            conn.commit()
        return name if name else "کاربر"
    except:
        return "کاربر ناشناس"

def get_clicker_name(clicker_id):
    try:
        chat = bot.get_chat(clicker_id)
        return f"{chat.first_name or ''} {chat.last_name or ''}".strip()
    except:
        return "کاربر ناشناس"

def save_trapped_history(owner_id, clicker_id, clicker_name, clicker_username):
    try:
        trapped_time = datetime.now().isoformat()
        c.execute("INSERT INTO trapped_history (owner_id, clicker_id, clicker_name, clicker_username, trapped_at) VALUES (?, ?, ?, ?, ?)",
                  (owner_id, clicker_id, clicker_name, clicker_username, trapped_time))
        conn.commit()
    except Exception as e:
        print(f"Error saving trapped history: {e}")

def delete_message_later(chat_id, message_id, delay, clicker_id, owner_name, report_id):
    time.sleep(delay)
    c.execute("SELECT status FROM cancel_payments WHERE report_id = ? AND status = 'paid'", (report_id,))
    if c.fetchone():
        return
    try:
        bot.delete_message(chat_id, message_id)
    except:
        pass
    c.execute("SELECT cancelled FROM pending_reports WHERE id = ?", (report_id,))
    result = c.fetchone()
    if result and result[0] == 0:
        c.execute("SELECT owner_id, clicker_id FROM pending_reports WHERE id = ?", (report_id,))
        row = c.fetchone()
        if row:
            owner_id, clicker_id = row
            clicker_name = get_clicker_name(clicker_id)
            try:
                chat = bot.get_chat(clicker_id)
                username = chat.username if chat.username else None
                save_trapped_history(owner_id, clicker_id, clicker_name, username)
            except:
                save_trapped_history(owner_id, clicker_id, clicker_name, None)
            
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
            final_message = f"⏰ **زمان شما تمام شد!**\n\nگزارش فضولی شما به {owner_name} ارسال گردید.\n\n❗️ **از پنل زیر استفاده کنید:**"
            try:
                bot.send_message(clicker_id, final_message, parse_mode='Markdown')
                main_panel(clicker_id)
            except:
                pass

# ---------- هندلر استارت (با پشتیبانی از لینک تبلیغاتی) ----------
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    text = message.text
    name = message.from_user.first_name
    if message.from_user.last_name:
        name += " " + message.from_user.last_name
    c.execute("INSERT OR IGNORE INTO users (telegram_id, link_code, user_name) VALUES (?, ?, ?)",
              (user_id, str(user_id), name))
    conn.commit()
    
    # ========== لینک تبلیغاتی (ad) ==========
    if text == "/start ad":
        keyboard = InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            InlineKeyboardButton("💬 پیام ناشناس", callback_data="ad_anon"),
            InlineKeyboardButton("📝 بیوگرافی", callback_data="ad_bio"),
            InlineKeyboardButton("📨 پیوی", callback_data="ad_pv"),
            InlineKeyboardButton("🖼 عکس پروفایل", callback_data="ad_photo")
        )
        
        ad_welcome = (
            "👋 **به ربات «کی داره نگاه میکنه؟» خوش اومدی!** 😂\n\n"
            "با این ربات می‌تونی بفهمی چه کسانی پروفایلت رو چک می‌کنن!\n\n"
            "🔹 **چطور کار میکنه؟**\n"
            "1️⃣ یک لینک اختصاصی از ربات می‌گیری\n"
            "2️⃣ لینک رو توی بیوگرافی یا کانالت می‌ذاری\n"
            "3️⃣ هرکی کلیک کنه، می‌فهمی کی بوده! 😉\n\n"
            "👇 **برای شروع، روی یکی از دکمه‌های زیر کلیک کن:**"
        )
        
        bot.send_message(user_id, ad_welcome, reply_markup=keyboard, parse_mode='Markdown')
        return
    
    # ========== لینک معمولی (track_xxx) ==========
    if text.startswith("/start track_"):
        code = text.split("track_")[1]
        owner_id = get_owner_id_by_code(code)
        clicker_id = user_id
        if owner_id and owner_id != clicker_id:
            owner_name = get_owner_name(owner_id)
            keyboard = InlineKeyboardMarkup()
            keyboard.add(InlineKeyboardButton("❌ عدم ارسال گزارش فضولی", callback_data=f"cancel_{code}_{clicker_id}"))
            
            c.execute("SELECT photo_id FROM user_photos WHERE user_id = ?", (owner_id,))
            photo_row = c.fetchone()
            trap_photo = photo_row[0] if photo_row and photo_row[0] else None
            
            trap_text = f"⚠️ **نباید این فضولی رو میکردی!**\n\nالان این فضولیت برای {owner_name} ارسال شد، بهتره قبل از اینکه بیاد ببینه، خودت بهش بگی داشتی فضولی میکردی 😊\n\nبرای عدم ارسال دکمه زیر را فشار دهید (فرصت شما 1 دقیقه و 15 ثانیه)"
            
            if trap_photo:
                msg = bot.send_photo(clicker_id, trap_photo, caption=trap_text, reply_markup=keyboard, parse_mode='Markdown')
            else:
                msg = bot.send_message(clicker_id, trap_text, reply_markup=keyboard, parse_mode='Markdown')
            
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
    link = generate_link(user_id)
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("📋 کپی لینک", callback_data=f"copy_link_{link}"))
    keyboard.add(InlineKeyboardButton("🔙 بازگشت به پنل", callback_data="back_to_panel"))
    bot.send_message(user_id, f"🔗 **لینک اختصاصی شما:**\n\n`{link}`\n\n✅ این لینک مخصوص شماست.\n📌 آن را در بیوگرافی یا کانال خود قرار دهید.\n⚠️ هر کسی روی این لینک کلیک کند، در تله می‌افتد!",
                     reply_markup=keyboard, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data.startswith("copy_link_"))
def copy_link_callback(call):
    bot.answer_callback_query(call.id, "✅ لینک با موفقیت کپی شد! (روی لینک نگه دارید و کپی کنید)", show_alert=True)

# ---------- دکمه‌های پنل ----------
@bot.message_handler(func=lambda message: message.text == "🍎 خرید سیر")
def handle_buy_apple(message):
    user_id = message.from_user.id
    hide_keyboard = ReplyKeyboardRemove()
    bot.send_message(user_id, "🍎 **خرید سیر**\n\nاین قابلیت به زودی اضافه می‌شود.\nبرای بازگشت به پنل، روی /start کلیک کنید.", reply_markup=hide_keyboard, parse_mode='Markdown')
    threading.Timer(2, lambda: main_panel(user_id)).start()

@bot.message_handler(func=lambda message: message.text == "🖼 تنظیم عکس مچ گیری")
def handle_set_photo(message):
    user_id = message.from_user.id
    hide_keyboard = ReplyKeyboardRemove()
    bot.send_message(user_id, "🖼 **تنظیم عکس مچ گیری**\n\nلطفاً عکس مورد نظر خود را ارسال کنید:", reply_markup=hide_keyboard, parse_mode='Markdown')
    bot.register_next_step_handler(message, save_photo)

def save_photo(message):
    user_id = message.from_user.id
    if message.photo:
        file_id = message.photo[-1].file_id
        c.execute("INSERT OR REPLACE INTO user_photos (user_id, photo_id) VALUES (?, ?)", (user_id, file_id))
        conn.commit()
        bot.send_message(user_id, "✅ عکس مچ‌گیری شما با موفقیت ذخیره شد!\nاز این پس هنگام کلیک روی لینک شما، این عکس نمایش داده می‌شود.")
    else:
        bot.send_message(user_id, "❌ لطفاً یک عکس معتبر ارسال کنید.")
    main_panel(user_id)

# ========== دکمه حذف عکس مچ گیری ==========
@bot.message_handler(func=lambda message: message.text == "🗑 حذف عکس مچ گیری")
def delete_trap_photo(message):
    user_id = message.from_user.id
    c.execute("DELETE FROM user_photos WHERE user_id = ?", (user_id,))
    conn.commit()
    bot.send_message(user_id, "🗑 عکس مچ‌گیری شما با موفقیت حذف شد.\nاز این پس هنگام کلیک روی لینک شما، عکسی نمایش داده نمی‌شود.")
    main_panel(user_id)

# ========== دکمه نمایش کاربران در تله افتاده اخیر ==========
@bot.message_handler(func=lambda message: message.text == "📋 کاربران در تله افتاده اخیر")
def show_trapped_list(message):
    user_id = message.from_user.id
    try:
        c.execute("SELECT clicker_name, clicker_username, trapped_at FROM trapped_history WHERE owner_id = ? ORDER BY trapped_at DESC LIMIT 20", (user_id,))
        rows = c.fetchall()
        if not rows:
            bot.send_message(user_id, "📭 هیچ کاربری تا کنون در تله شما نیفتاده است.")
            return
        text = "📋 لیست کاربرانی که در تله شما افتاده‌اند (اخیر):\n\n"
        for i, row in enumerate(rows, 1):
            name = row[0] if row[0] else "نامشخص"
            username = row[1] if row[1] else "ندارد"
            trapped_str = row[2]
            try:
                if 'T' in trapped_str:
                    trapped_dt = datetime.fromisoformat(trapped_str)
                else:
                    trapped_dt = datetime.strptime(trapped_str, '%Y-%m-%d %H:%M:%S.%f')
            except:
                time_str = trapped_str
            else:
                time_str = trapped_dt.strftime('%Y/%m/%d %H:%M:%S')
            text += f"{i}. 👤 نام: {name}\n🆔 یوزرنیم: @{username if username != 'ندارد' else 'ندارد'}\n📅 زمان: {time_str}\n\n"
        if len(text) > 4000:
            parts = [text[i:i+4000] for i in range(0, len(text), 4000)]
            for part in parts:
                bot.send_message(user_id, part)
        else:
            bot.send_message(user_id, text)
    except Exception as e:
        bot.send_message(user_id, f"❌ خطا در نمایش تاریخچه: {e}")
        print(f"Error in show_trapped_list: {e}")

# ========== دکمه راهنما ==========
@bot.message_handler(func=lambda message: message.text == "❓ راهنما")
def handle_help(message):
    user_id = message.from_user.id
    help_text = (
        "📚 **راهنمای جامع استفاده از ربات**\n\n"
        "با سلام و احترام. به بخش راهنمای ربات خوش آمدید. در این بخش با تمامی امکانات و نحوه عملکرد دقیق ربات آشنا خواهید شد:\n\n"
        "**۱. نحوه کارکرد ربات (سیستم مچ‌گیری):**\n"
        "شما می‌توانید با دریافت لینک اختصاصی خود از طریق ربات و قرار دادن آن در بخش بیوگرافی (Bio) حساب کاربری‌تان، متوجه شوید چه کسانی در حال بازدید از پروفایل شما هستند.\n"
        "🔹 *نکته:* امکان قرار دادن این لینک به صورت مخفی (Hyperlink) در بیوگرافی وجود دارد.\n"
        "به محض اینکه شخصی از روی کنجکاوی روی لینک شما کلیک کرده و وارد ربات شود، ربات فوراً پیامی با مضمون «یک فضول در تله افتاد!» برای شما ارسال می‌کند. این گزارش شامل اطلاعات کامل شخص است:\n"
        "▫️ نام کاربر\n"
        "▫️ آیدی (لینک ورود به پیوی)\n"
        "▫️ عکس پروفایل\n"
        "▫️ بیوگرافی (در صورت وجود)\n\n"
        "**۲. امکانات رایگان (بدون نیاز به اشتراک):**\n"
        "🔹 **ارسال پیام ناشناس:** می‌توانید از طریق ربات، برای شخصی که در تله شما افتاده است به صورت کاملاً ناشناس پیام ارسال کنید.\n"
        "🔹 **مشاهده بیوگرافی و عکس پروفایل و آیدی کاربران:** همه این قابلیت‌ها به صورت رایگان در دسترس است.\n\n"
        "**۳. شخصی‌سازی تله (عکس مچ‌گیری):**\n"
        "شما می‌توانید واکنش ربات به فردی که در تله می‌افتد را شخصی‌سازی کنید:\n"
        "🔹 **تنظیم عکس مچ‌گیری:** یک تصویر دلخواه تنظیم کنید تا به محض ورود شخص، آن عکس نیز برای وی ارسال شود.\n\n"
        "💬 در صورت بروز هرگونه مشکل یا داشتن سوالات بیشتر، با @Asd00120A در ارتباط باشید."
    )
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("🔙 بازگشت به پنل", callback_data="back_to_panel"))
    hide_keyboard = ReplyKeyboardRemove()
    bot.send_message(user_id, help_text, reply_markup=keyboard, parse_mode='Markdown')

# ========== پیام ناشناس رایگان ==========
@bot.callback_query_handler(func=lambda call: call.data.startswith("anon_"))
def anonymous_message(call):
    _, clicker_id, owner_id = call.data.split("_")
    clicker_id, owner_id = int(clicker_id), int(owner_id)
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
    bot.send_message(user_id, "💬 **ارسال پیام ناشناس**\n\nلطفاً متن پیام خود را ارسال کنید.\nاین پیام **به صورت ناشناس** برای کاربر فضول فرستاده خواهد شد.\n\n⚠️ توجه: نام و اطلاعات شما فاش نمی‌شود.",
                     reply_markup=cancel_keyboard, parse_mode='Markdown')
    bot.register_next_step_handler_by_chat_id(user_id, receive_anonymous_message, clicker_id, user_id)
    bot.answer_callback_query(call.id)

def receive_anonymous_message(message, clicker_id, owner_id):
    user_id = message.from_user.id
    if user_id not in anonymous_temp:
        return
    if message.text:
        anonymous_text = message.text
        try:
            bot.send_message(clicker_id, f"💌 **پیام ناشناس**\n\nیک کاربر ناشناس به شما پیام داده است:\n\n「 {anonymous_text} 」\n\n🔹 شما نمی‌توانید پاسخ دهید.", parse_mode='Markdown')
            bot.send_message(user_id, "✅ **پیام شما با موفقیت ارسال شد!**\n\nپیام شما به صورت ناشناس برای کاربر فضول فرستاده شد.", parse_mode='Markdown')
        except Exception as e:
            bot.send_message(user_id, f"❌ **خطا در ارسال پیام**\n\nکاربر فضول ممکن است ربات را بلاک کرده باشد.\n\nخطا: {e}", parse_mode='Markdown')
    else:
        bot.send_message(user_id, "❌ لطفاً فقط متن ارسال کنید.", parse_mode='Markdown')
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

# ========== دکمه‌های تله و پرداخت تستی ==========
@bot.callback_query_handler(func=lambda call: call.data.startswith("cancel_"))
def cancel_report_payment_page(call):
    _, code, clicker_id = call.data.split("_")
    clicker_id = int(clicker_id)
    if call.from_user.id != clicker_id:
        bot.answer_callback_query(call.id, "این دکمه مال تو نیست!", show_alert=True)
        return
    c.execute("SELECT id FROM pending_reports WHERE link_code = ? AND clicker_id = ? AND cancelled = FALSE ORDER BY id DESC LIMIT 1", (code, clicker_id))
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
    keyboard.add(InlineKeyboardButton("📄 مشاهده جزئیات", callback_data=f"details_{report_id}"), InlineKeyboardButton("💳 پرداخت", callback_data=f"fake_pay_{report_id}"))
    payment_text = "💳 **درخواست پول**\n\n**لغو ارسال گزارش فضولی**\nبا پرداخت فقط ۶,۵۰۰ تومان، گزارش فضولی شما برای صاحب لینک ارسال نخواهد شد.\n\nلغو گزارش: 65000\nمبلغ: ۶۵,۰۰۰ ریال"
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
    bot.send_message(call.message.chat.id, "✅ **پرداخت شما با موفقیت تایید شد!**\n\nگزارش فضولی شما لغو گردید و برای صاحب لینک ارسال نخواهد شد.\n\n🙏 از شما متشکریم.", parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data.startswith("details_"))
def show_details(call):
    _, report_id = call.data.split("_")
    bot.answer_callback_query(call.id)
    details_msg = "📋 **جزئیات پرداخت**\n\n💰 مبلغ: ۶,۵۰۰ تومان (۶۵,۰۰۰ ریال)\n📝 دلیل: لغو ارسال گزارش فضولی\n⏱ زمان باقی مانده: کمتر از ۷۵ ثانیه\n\nپس از پرداخت موفق، گزارش شما ارسال نخواهد شد."
    bot.send_message(call.message.chat.id, details_msg, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data == "back_to_panel")
def back_to_panel_inline(call):
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except:
        pass
    main_panel(call.from_user.id)
    bot.answer_callback_query(call.id)

# ========== ۴ دکمه اصلی (بیوگرافی، پیوی، عکس) بدون بررسی اشتراک ==========
@bot.callback_query_handler(func=lambda call: call.data.startswith("bio_"))
def show_bio(call):
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
    parts = call.data.split("_")
    if len(parts) < 3:
        bot.answer_callback_query(call.id, "خطا در اطلاعات!", show_alert=True)
        return
    clicker_id = int(parts[1])
    bot.answer_callback_query(call.id)
    try:
        chat = bot.get_chat(clicker_id)
        username = chat.username
        if username:
            user_info = f"🆔 آیدی کاربر فضول:\n@{username}"
        else:
            user_info = f"🆔 آیدی کاربر فضول:\n{clicker_id}"
        bot.send_message(call.message.chat.id, user_info)
    except Exception as e:
        bot.send_message(call.message.chat.id, f"❌ امکان دریافت آیدی کاربر وجود ندارد.\nخطا: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith("photo_"))
def show_photo(call):
    _, clicker_id, owner_id = call.data.split("_")
    bot.answer_callback_query(call.id)
    try:
        photos = bot.get_user_profile_photos(int(clicker_id), limit=1)
        if photos.total_count > 0:
            file_id = photos.photos[0][-1].file_id
            bot.send_photo(call.message.chat.id, file_id, caption="🖼 عکس پروفایل کاربر")
        else:
            bot.send_message(call.message.chat.id, "❌ این کاربر عکس پروفایل ندارد.")
    except:
        bot.send_message(call.message.chat.id, "❌ امکان نمایش عکس وجود ندارد.")

# ========== دکمه‌های تبلیغاتی (ad) ==========
@bot.callback_query_handler(func=lambda call: call.data.startswith("ad_"))
def ad_buttons(call):
    action = call.data.split("_")[1]
    user_id = call.from_user.id
    
    messages = {
        "anon": "💬 **پیام ناشناس**\n\nبا دریافت لینک اختصاصی، می‌تونی به کاربرای فضول پیام ناشناس بدی!",
        "bio": "📝 **بیوگرافی**\n\nبا دریافت لینک اختصاصی، می‌تونی ببینی چه کسانی بیوگرافیت رو چک می‌کنن!",
        "pv": "📨 **پیوی**\n\nبا دریافت لینک اختصاصی، می‌تونی بفهمی چه کسانی پیوی‌ات رو چک می‌کنن!",
        "photo": "🖼 **عکس پروفایل**\n\nببین چه کسانی عکس پروفایلت رو می‌بینن!"
    }
    
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("🔗 دریافت لینک من", callback_data="get_my_link"))
    
    bot.send_message(
        user_id,
        messages.get(action, "❌ گزینه نامعتبر!"),
        reply_markup=keyboard,
        parse_mode='Markdown'
    )
    
    bot.answer_callback_query(call.id, "✅")

# ========== پنل مدیریت (با دکمه‌های جدید) ==========
@bot.message_handler(commands=['admin'])
def admin_panel(message):
    user_id = message.from_user.id
    if user_id not in ADMIN_IDS:
        bot.reply_to(message, "❌ شما دسترسی به این بخش ندارید!")
        return
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("📊 آمار ربات", callback_data="admin_stats"),
        InlineKeyboardButton("👥 لیست کاربران", callback_data="admin_users"),
        InlineKeyboardButton("📋 گزارش‌های تله", callback_data="admin_reports"),
        InlineKeyboardButton("🖼 عکس‌های ذخیره شده", callback_data="admin_photos"),
        InlineKeyboardButton("📢 تبلیغات", callback_data="admin_advertise"),
        InlineKeyboardButton("📢 ارسال به همه کاربران", callback_data="admin_broadcast"),
        InlineKeyboardButton("🗑 پاک کردن دیتابیس", callback_data="admin_clear"),
        InlineKeyboardButton("🔙 بستن پنل", callback_data="admin_close")
    )
    
    text = (
        "🔐 **پنل مدیریت ربات**\n\n"
        "سلام ادمین گرامی! از دکمه‌های زیر استفاده کن:\n\n"
        f"👤 کل کاربران: {get_total_users()}\n"
        f"📊 گزارش‌های فعال: {get_total_reports()}\n"
        f"🎯 کاربران در تله رفته: {get_total_trapped()}\n"
        f"🖼 عکس‌های مچ‌گیری: {get_total_photos()}"
    )
    
    bot.send_message(user_id, text, reply_markup=keyboard, parse_mode='Markdown')

# ========== هندلر ارسال همگانی (Broadcast) ==========
@bot.callback_query_handler(func=lambda call: call.data == "admin_broadcast")
def admin_broadcast(call):
    user_id = call.from_user.id
    if user_id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "❌ شما دسترسی ندارید!", show_alert=True)
        return
    
    bot.answer_callback_query(call.id, "📝 لطفاً متن پیام خود را ارسال کنید.")
    msg = bot.send_message(user_id, "📤 **ارسال به همه کاربران**\n\nلطفاً متن پیامی که می‌خواهید برای **همه کاربران** ارسال شود را وارد کنید.\n\n⚠️ می‌توانید از **مارک‌داون** و **لینک** استفاده کنید.\n\nبرای لغو، /cancel را بفرستید.", parse_mode='Markdown')
    bot.register_next_step_handler_by_chat_id(user_id, broadcast_get_message, user_id, msg.message_id)

def broadcast_get_message(message, admin_id, prompt_msg_id):
    user_id = message.from_user.id
    if user_id not in ADMIN_IDS:
        return
    
    if message.text == "/cancel":
        bot.send_message(user_id, "❌ عملیات ارسال همگانی لغو شد.")
        try:
            bot.delete_message(user_id, prompt_msg_id)
        except:
            pass
        admin_panel(message)
        return
    
    broadcast_text = message.text
    try:
        bot.delete_message(user_id, prompt_msg_id)
    except:
        pass
    
    c.execute("SELECT telegram_id FROM users")
    users = c.fetchall()
    total = len(users)
    
    if total == 0:
        bot.send_message(user_id, "📭 هیچ کاربری در دیتابیس وجود ندارد!")
        admin_panel(message)
        return
    
    bot.send_message(user_id, f"⏳ در حال ارسال پیام به {total} کاربر... لطفاً صبر کنید.")
    
    success = 0
    failed = 0
    
    for idx, (uid,) in enumerate(users, 1):
        try:
            bot.send_message(uid, broadcast_text, parse_mode='Markdown')
            success += 1
        except Exception as e:
            failed += 1
            print(f"Failed to send to {uid}: {e}")
        
        if idx % 30 == 0:
            time.sleep(0.5)
    
    report = (
        "✅ **ارسال همگانی کامل شد!**\n\n"
        f"👤 کل کاربران: {total}\n"
        f"✅ ارسال موفق: {success}\n"
        f"❌ ارسال ناموفق: {failed}"
    )
    bot.send_message(user_id, report, parse_mode='Markdown')
    admin_panel(message)

# ========== تبلیغات (دریافت لینک از ادمین و ساخت پیام تله) ==========
@bot.callback_query_handler(func=lambda call: call.data == "admin_advertise")
def admin_advertise(call):
    user_id = call.from_user.id
    if user_id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "❌ شما دسترسی ندارید!", show_alert=True)
        return
    
    # حذف پیام قبلی پنل
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        pass
    
    # دکمه انصراف
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("❌ انصراف", callback_data="cancel_ad"))
    
    msg = bot.send_message(
        user_id,
        "📢 **ساخت پیام تبلیغاتی**\n\n"
        "لطفاً لینک کانال یا گروه مورد نظر را ارسال کنید.\n\n"
        "مثال: `https://t.me/your_channel`\n\n"
        "⚠️ این لینک در دکمه‌های پیام قرار داده خواهد شد.",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )
    
    # منتظر دریافت لینک از ادمین
    bot.register_next_step_handler_by_chat_id(user_id, receive_ad_link, user_id, msg.message_id)
    bot.answer_callback_query(call.id, "✅ لطفاً لینک را ارسال کنید.")

def receive_ad_link(message, admin_id, prompt_msg_id):
    user_id = message.from_user.id
    if user_id not in ADMIN_IDS:
        return
    
    # حذف پیام راهنما
    try:
        bot.delete_message(admin_id, prompt_msg_id)
    except:
        pass
    
    # دریافت لینک
    link = message.text.strip()
    
    # اعتبارسنجی ساده
    if not link.startswith("http"):
        bot.send_message(admin_id, "❌ لینک نامعتبر! لطفاً با `https://` شروع کنید.")
        admin_panel(message)  # برگشت به پنل
        return
    
    # ساخت پیام تله با لینک دریافتی
    trap_message = (
        "🎯 **یک فضول در تله افتاد!**\n\n"
        "👤 نام: سارا\n"
        "⏰ زمان: 12:48"
    )
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("💬 پیام ناشناس", url=link),
        InlineKeyboardButton("📝 بیوگرافی", url=link),
        InlineKeyboardButton("📨 پیوی", url=link),
        InlineKeyboardButton("🖼 پروفایل", url=link)
    )
    
    # ارسال پیام نهایی به ادمین (بدون هیچ دکمه اضافی)
    bot.send_message(
        admin_id,
        trap_message,
        reply_markup=keyboard,
        parse_mode='Markdown'
    )
    
    # اطلاع به ادمین
    bot.send_message(admin_id, "✅ پیام تبلیغاتی ساخته شد! می‌توانید آن را برای کاربران فوروارد کنید.")
    
    # برگشت به پنل ادمین
    admin_panel(message)

@bot.callback_query_handler(func=lambda call: call.data == "cancel_ad")
def cancel_ad(call):
    user_id = call.from_user.id
    if user_id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "❌ شما دسترسی ندارید!", show_alert=True)
        return
    
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        pass
    
    bot.send_message(user_id, "❌ عملیات ساخت تبلیغ لغو شد.")
    admin_panel(call.message)  # برگشت به پنل
    bot.answer_callback_query(call.id, "✅ لغو شد")

# ---------- بقیه بخش‌های پنل مدیریت (بدون تغییر) ----------
@bot.callback_query_handler(func=lambda call: call.data == "admin_stats")
def admin_stats(call):
    user_id = call.from_user.id
    if user_id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "❌ شما دسترسی ندارید!", show_alert=True)
        return
    text = (
        "📊 **آمار کامل ربات**\n\n"
        f"👤 کل کاربران: {get_total_users()}\n"
        f"📝 گزارش‌های در انتظار: {get_total_reports()}\n"
        f"🎯 کاربران در تله رفته: {get_total_trapped()}\n"
        f"🖼 عکس‌های مچ‌گیری: {get_total_photos()}"
    )
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode='Markdown')
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "admin_users")
def admin_users(call):
    user_id = call.from_user.id
    if user_id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "❌ شما دسترسی ندارید!", show_alert=True)
        return
    c.execute("SELECT telegram_id, user_name, link_code FROM users ORDER BY telegram_id DESC LIMIT 30")
    users = c.fetchall()
    if not users:
        text = "📭 هیچ کاربری در دیتابیس یافت نشد."
    else:
        text = "👥 **لیست ۳۰ کاربر اخیر:**\n\n"
        for uid, name, code in users:
            display_name = name if name else "بدون نام"
            text += f"• {display_name} (ID: `{uid}`)\n"
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode='Markdown')
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "admin_reports")
def admin_reports(call):
    user_id = call.from_user.id
    if user_id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "❌ شما دسترسی ندارید!", show_alert=True)
        return
    c.execute("SELECT id, owner_id, clicker_id, expires_at, cancelled FROM pending_reports ORDER BY id DESC LIMIT 20")
    reports = c.fetchall()
    if not reports:
        text = "📭 هیچ گزارشی یافت نشد."
    else:
        text = "📋 **۲۰ گزارش اخیر:**\n\n"
        for rid, owner_id, clicker_id, expires_at, cancelled in reports:
            owner_name = get_owner_name(owner_id)
            clicker_name = get_clicker_name(clicker_id)
            status = "❌ لغو شده" if cancelled else "⏳ در انتظار"
            text += f"• {owner_name} ← {clicker_name} [{status}]\n"
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode='Markdown')
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "admin_photos")
def admin_photos(call):
    user_id = call.from_user.id
    if user_id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "❌ شما دسترسی ندارید!", show_alert=True)
        return
    c.execute("SELECT user_id, photo_id FROM user_photos LIMIT 20")
    photos = c.fetchall()
    if not photos:
        text = "📭 هیچ عکسی ذخیره نشده است."
    else:
        text = "🖼 **آخرین عکس‌های ذخیره شده:**\n\n"
        for uid, pid in photos:
            name = get_owner_name(uid)
            text += f"• {name} (ID: `{uid}`)\n"
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode='Markdown')
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "admin_clear")
def admin_clear(call):
    user_id = call.from_user.id
    if user_id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "❌ شما دسترسی ندارید!", show_alert=True)
        return
    keyboard = InlineKeyboardMarkup()
    keyboard.add(
        InlineKeyboardButton("✅ بله، پاک کن!", callback_data="admin_confirm_clear"),
        InlineKeyboardButton("❌ نه، انصراف", callback_data="admin_close")
    )
    bot.edit_message_text(
        "⚠️ **هشدار جدی!**\n\nآیا از پاک کردن تمام دیتابیس مطمئنی؟\n\n"
        "❗️ این عمل غیرقابل بازگشت است و تمام اطلاعات زیر حذف می‌شوند:\n"
        "• لیست کاربران\n"
        "• گزارش‌های تله\n"
        "• عکس‌های مچ‌گیری\n"
        "• تاریخچه کاربرانی که در تله افتاده‌اند",
        call.message.chat.id, call.message.message_id,
        reply_markup=keyboard, parse_mode='Markdown'
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "admin_confirm_clear")
def admin_confirm_clear(call):
    user_id = call.from_user.id
    if user_id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "❌ شما دسترسی ندارید!", show_alert=True)
        return
    c.execute("DELETE FROM users")
    c.execute("DELETE FROM pending_reports")
    c.execute("DELETE FROM user_photos")
    c.execute("DELETE FROM trapped_history")
    conn.commit()
    bot.edit_message_text(
        "✅ **دیتابیس با موفقیت پاک شد!**\n\n"
        "تمامی اطلاعات کاربران و گزارش‌ها حذف گردید.",
        call.message.chat.id, call.message.message_id,
        parse_mode='Markdown'
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "admin_close")
def admin_close(call):
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        bot.edit_message_text("🔒 پنل مدیریت بسته شد.", call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id)

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
    bot.remove_webhook()
    time.sleep(1)
    webhook_url = f"{BASE_URL}/webhook"
    if bot.set_webhook(url=webhook_url):
        print(f"✅ Webhook set successfully to {webhook_url}")
    else:
        print(f"❌ Failed to set webhook to {webhook_url}")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    set_webhook()
    app.run(host='0.0.0.0', port=port)

