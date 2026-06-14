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
BASE_URL = "https://habib-q5vo.onrender.com"

# ---------- کانال الزامی ----------
REQUIRED_CHANNEL = "@film01385"

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

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

# جدول پرداخت‌های لغو گزارش (برای fake_pay)
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

# ========== بررسی عضویت در کانال ==========
def check_membership(user_id):
    try:
        member = bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except:
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

# ========== پنل اصلی (بدون دکمه اشتراک) ==========
def main_panel(user_id, message_id=None):
    keyboard = ReplyKeyboardMarkup(row_width=2, resize_keyboard=True, one_time_keyboard=False)
    btn_get_link = KeyboardButton("🔗 دریافت لینک من")
    btn_buy_apple = KeyboardButton("🍎 خرید سیر")
    btn_set_photo = KeyboardButton("🖼 تنظیم عکس مچ گیری")
    btn_set_text = KeyboardButton("📝 تنظیم متن مچ گیری")
    btn_del_photo = KeyboardButton("🗑 حذف عکس مچ گیری")
    btn_del_text = KeyboardButton("🗑 حذف متن مچ گیری")
    btn_trapped_list = KeyboardButton("📋 کاربران در تله افتاده اخیر")
    btn_help = KeyboardButton("❓ راهنما")
    
    keyboard.add(btn_get_link)
    keyboard.add(btn_buy_apple)
    keyboard.add(btn_set_photo, btn_set_text)
    keyboard.add(btn_del_photo, btn_del_text)
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
    try:
        chat = bot.get_chat(owner_id)
        return f"{chat.first_name or ''} {chat.last_name or ''}".strip()
    except:
        return "صاحب پروفایل"

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
            
            c.execute("SELECT text FROM user_texts WHERE user_id = ?", (owner_id,))
            text_row = c.fetchone()
            c.execute("SELECT photo_id FROM user_photos WHERE user_id = ?", (owner_id,))
            photo_row = c.fetchone()
            
            trap_text = None
            trap_photo = None
            
            if text_row and text_row[0]:
                trap_text = text_row[0]
            if photo_row and photo_row[0]:
                trap_photo = photo_row[0]
            
            if not trap_text:
                trap_text = f"⚠️ **نباید این فضولی رو میکردی!**\n\nالان این فضولیت برای {owner_name} ارسال شد، بهتره قبل از اینکه بیاد ببینه، خودت بهش بگی داشتی فضولی میکردی 😊\n\nبرای عدم ارسال دکمه زیر را فشار دهید (فرصت شما 1 دقیقه و 15 ثانیه)"
            
            if trap_photo:
                msg = bot.send_photo(
                    clicker_id,
                    trap_photo,
                    caption=trap_text,
                    reply_markup=keyboard,
                    parse_mode='Markdown'
                )
            else:
                msg = bot.send_message(
                    clicker_id,
                    trap_text,
                    reply_markup=keyboard,
                    parse_mode='Markdown'
                )
            
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
    bot.send_message(user_id, f"🔗 **لینک اختصاصی شما:**\n\n`{link}`\n\n✅ این لینک مخصوص شماست.\n📌 آن را در بیوگرافی یا کانال خود قرار دهید.\n⚠️ هر کسی روی این لینک کلیک کند، در تله می‌افتد!",
                     reply_markup=keyboard, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data.startswith("copy_link_"))
def copy_link_callback(call):
    bot.answer_callback_query(call.id, "✅ لینک با موفقیت کپی شد! (روی لینک نگه دارید و کپی کنید)", show_alert=True)

# ---------- دکمه‌های پنل ----------
@bot.message_handler(func=lambda message: message.text == "🍎 خرید سیر")
def handle_buy_apple(message):
    user_id = message.from_user.id
    if not require_channel(user_id):
        return
    hide_keyboard = ReplyKeyboardRemove()
    bot.send_message(user_id, "🍎 **خرید سیر**\n\nاین قابلیت به زودی اضافه می‌شود.\nبرای بازگشت به پنل، روی /start کلیک کنید.", reply_markup=hide_keyboard, parse_mode='Markdown')
    threading.Timer(2, lambda: main_panel(user_id)).start()

@bot.message_handler(func=lambda message: message.text == "🖼 تنظیم عکس مچ گیری")
def handle_set_photo(message):
    user_id = message.from_user.id
    if not require_channel(user_id):
        return
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

@bot.message_handler(func=lambda message: message.text == "📝 تنظیم متن مچ گیری")
def handle_set_text(message):
    user_id = message.from_user.id
    if not require_channel(user_id):
        return
    hide_keyboard = ReplyKeyboardRemove()
    bot.send_message(user_id, "📝 **تنظیم متن مچ گیری**\n\nلطفاً متن مورد نظر خود را ارسال کنید:", reply_markup=hide_keyboard, parse_mode='Markdown')
    bot.register_next_step_handler(message, save_text)

def save_text(message):
    user_id = message.from_user.id
    text = message.text
    c.execute("INSERT OR REPLACE INTO user_texts (user_id, text) VALUES (?, ?)", (user_id, text))
    conn.commit()
    bot.send_message(user_id, f"✅ متن مچ‌گیری شما با موفقیت ذخیره شد!\nاز این پس هنگام کلیک روی لینک شما، این متن نمایش داده می‌شود.\n\nمتن شما:\n{text}")
    main_panel(user_id)

# ========== دکمه‌های حذف عکس و متن مچ‌گیری ==========
@bot.message_handler(func=lambda message: message.text == "🗑 حذف عکس مچ گیری")
def delete_trap_photo(message):
    user_id = message.from_user.id
    if not require_channel(user_id):
        return
    c.execute("DELETE FROM user_photos WHERE user_id = ?", (user_id,))
    conn.commit()
    bot.send_message(user_id, "🗑 عکس مچ‌گیری شما با موفقیت حذف شد.\nاز این پس هنگام کلیک روی لینک شما، عکسی نمایش داده نمی‌شود.")
    main_panel(user_id)

@bot.message_handler(func=lambda message: message.text == "🗑 حذف متن مچ گیری")
def delete_trap_text(message):
    user_id = message.from_user.id
    if not require_channel(user_id):
        return
    c.execute("DELETE FROM user_texts WHERE user_id = ?", (user_id,))
    conn.commit()
    bot.send_message(user_id, "🗑 متن مچ‌گیری شما با موفقیت حذف شد.\nاز این پس هنگام کلیک روی لینک شما، متن پیش‌فرض نمایش داده می‌شود.")
    main_panel(user_id)

# ========== دکمه نمایش کاربران در تله افتاده اخیر ==========
@bot.message_handler(func=lambda message: message.text == "📋 کاربران در تله افتاده اخیر")
def show_trapped_list(message):
    user_id = message.from_user.id
    if not require_channel(user_id):
        return
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
    if not require_channel(user_id):
        return
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
        "**۳. شخصی‌سازی تله (متن و عکس مچ‌گیری):**\n"
        "شما می‌توانید واکنش ربات به فردی که در تله می‌افتد را کاملاً شخصی‌سازی کنید:\n"
        "🔹 **تنظیم متن مچ‌گیری:** پیامی که فرد به محض کلیک روی لینک شما دریافت می‌کند را تغییر دهید.\n"
        "🔹 **تنظیم عکس مچ‌گیری:** علاوه بر متن، می‌توانید یک تصویر دلخواه تنظیم کنید تا به محض ورود شخص، آن عکس نیز برای وی ارسال شود.\n\n"
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
