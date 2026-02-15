import os
import re
import asyncio
import aiohttp
import logging
import base64
from pyrogram import Client, filters, idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import UserNotParticipant
from motor.motor_asyncio import AsyncIOMotorClient
from bson.objectid import ObjectId
from aiohttp import web
from urllib.parse import quote, unquote

# ================= CONFIGURATION =================
def get_clean_var(key, default=""):
    val = os.environ.get(key, default)
    return str(val).strip()

API_ID = int(get_clean_var("API_ID", "0"))
API_HASH = get_clean_var("API_HASH", "")
BOT_TOKEN = get_clean_var("BOT_TOKEN", "")
MONGO_URL = get_clean_var("MONGO_URL", "")
TMDB_API_KEY = get_clean_var("TMDB_API_KEY", "") # Yahan apni TMDB API Key dalen

ADMIN_IDS = [int(x) for x in get_clean_var("ADMIN_IDS", "0").split()]
STORAGE_CHANNEL = int(get_clean_var("STORAGE_CHANNEL", "0")) 
SEARCH_CHAT = int(get_clean_var("SEARCH_CHAT", "0")) 
FSUB_CHANNEL = int(get_clean_var("FSUB_CHANNEL", "0")) 
MAIN_CHANNEL_LINK = get_clean_var("MAIN_CHANNEL_LINK", "https://t.me/Movies2026Cinema")

SHORT_DOMAIN = get_clean_var("SHORT_DOMAIN", "arolinks.com")
SHORT_API_KEY = get_clean_var("SHORT_API_KEY", "")

# Settings
SHORTLINK_ENABLED = True 
PAGE_SIZE = 6 

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= BOT CLIENT =================
class MovieBot(Client):
    def __init__(self):
        super().__init__("pratap_session", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
        self.movies = None

    async def start(self):
        await super().start()
        try:
            mongo_client = AsyncIOMotorClient(MONGO_URL)
            db = mongo_client["PratapCinemaBot"]
            self.movies = db["movies"]
            print("✅ MongoDB Connected!")
        except Exception as e:
            print(f"❌ MongoDB Error: {e}")
        print(f"🚀 BOT STARTED as @{self.me.username}")

    async def stop(self, *args):
        await super().stop()
        print("Bot Stopped.")

app = MovieBot()

# ================= HELPERS =================

async def get_poster(query):
    if not TMDB_API_KEY: return None
    try:
        url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={quote(query)}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.json()
                if data.get("results"):
                    poster_path = data["results"][0].get("poster_path")
                    if poster_path:
                        return f"https://image.tmdb.org/t/p/w500{poster_path}"
    except Exception as e:
        logger.error(f"TMDB Error: {e}")
    return None

async def get_shortlink(url):
    if not SHORTLINK_ENABLED: return url
    try:
        api_url = f"https://{SHORT_DOMAIN}/api?api={SHORT_API_KEY}&url={url}"
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=10) as resp:
                res = await resp.json()
                if res.get("status") == "success": return res["shortenedUrl"]
    except: pass
    return url

def clean_name(text):
    if not text: return ""
    text = text.lower()
    junk = [r'\(.*?\)', r'\[.*?\]', '1080p', '720p', '480p', 'x264', 'x265', 'hevc', 'hindi', 'english', 'dual audio', 'web-dl', 'bluray', 'camrip', 'pre-dvd']
    for word in junk: text = re.sub(word, '', text)
    return " ".join(text.replace(".", " ").replace("_", " ").split()).strip()

async def get_search_buttons(query, results, offset=0):
    btn_list = []
    me = await app.get_me()
    for res in results[offset : offset + PAGE_SIZE]:
        db_id = str(res["_id"])
        db_title = res["title"]
        display_name = db_title[:35] + "..." if len(db_title) > 35 else db_title
        bot_url = f"https://t.me/{me.username}?start=file_{db_id}"
        final_link = await get_shortlink(bot_url)
        btn_list.append([InlineKeyboardButton(f"🎬 {display_name}", url=final_link)])

    nav_btns = []
    if offset > 0:
        nav_btns.append(InlineKeyboardButton("⬅️ Back", callback_data=f"page_{offset - PAGE_SIZE}_{quote(query)}"))
    if offset + PAGE_SIZE < len(results):
        nav_btns.append(InlineKeyboardButton("Next ➡️", callback_data=f"page_{offset + PAGE_SIZE}_{quote(query)}"))
    
    if nav_btns: btn_list.append(nav_btns)
    
    query_b64 = base64.urlsafe_b64encode(query.encode()).decode().rstrip("=")
    btn_list.append([InlineKeyboardButton("📂 GET ALL FILES (IN PM) 📂", url=f"https://t.me/{me.username}?start=all_{query_b64}")])
    return InlineKeyboardMarkup(btn_list)

async def delete_after_delay(msgs, delay):
    await asyncio.sleep(delay)
    for m in msgs:
        try: await m.delete()
        except: pass

# ================= AUTO-FILTER SEARCH (GROUP) =================

@app.on_message(filters.chat(SEARCH_CHAT) & filters.text & ~filters.command(["start", "pratap", "delall", "del", "shortlink"]))
async def search_movie(client, msg):
    query = clean_name(msg.text)
    if len(query) < 2: return
    sm = await client.send_message(msg.chat.id, "🔍 Searching...")

    cursor = client.movies.find({"title": {"$regex": query, "$options": "i"}})
    results = await cursor.to_list(length=100)

    if not results:
        words = query.split()
        if words:
            keyword = max(words, key=len)
            cursor = client.movies.find({"title": {"$regex": keyword, "$options": "i"}})
            results = await cursor.to_list(length=100)

    if not results:
        await sm.edit(f"❌ `{msg.text}` nahi mili!")
        asyncio.create_task(delete_after_delay([sm, msg], 15))
        return 

    # TMDB Poster fetch kar rahe hain
    poster = await get_poster(query)
    markup = await get_search_buttons(query, results, offset=0)
    
    text = f"🎬 **Results for:** `{msg.text}`\n\n⏳ _Ye result 5 minute mein delete ho jayega._"
    
    try:
        if poster:
            res_msg = await client.send_photo(msg.chat.id, photo=poster, caption=text, reply_markup=markup)
        else:
            res_msg = await client.send_message(msg.chat.id, text=text, reply_markup=markup)
    except:
        res_msg = await client.send_message(msg.chat.id, text=text, reply_markup=markup)

    await sm.delete()
    try: await msg.delete()
    except: pass
    asyncio.create_task(delete_after_delay([res_msg], 300))

# ================= START HANDLER (PM) =================

@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, msg):
    data = msg.command[1] if len(msg.command) > 1 else ""

    # FSUB Check
    try:
        await client.get_chat_member(FSUB_CHANNEL, msg.from_user.id)
    except UserNotParticipant:
        invite = (await client.get_chat(FSUB_CHANNEL)).invite_link or MAIN_CHANNEL_LINK
        me = await client.get_me()
        buttons = [[InlineKeyboardButton("📢 JOIN CHANNEL 📢", url=invite)]]
        if data:
            try_again_link = f"https://t.me/{me.username}?start={data}"
            buttons.append([InlineKeyboardButton("🔄 TRY AGAIN / VERIFY 🔄", url=try_again_link)])
        btn = InlineKeyboardMarkup(buttons)
        return await msg.reply("❌ Pehle channel join karein!", reply_markup=btn)
    except: pass

    if not data: return await msg.reply("👋 Namaste! Group me search karein.")

    if data.startswith("file_"):
        res = await client.movies.find_one({"_id": ObjectId(data.split("_")[1])})
        if res:
            cap = f"📂 `{res['title']}`\n\n⚠️ **5 min mein delete ho jayegi.**"
            sf = await client.send_cached_media(msg.chat.id, res["file_id"], caption=cap)
            asyncio.create_task(delete_after_delay([sf], 300))
            
    elif data.startswith("all_"):
        try:
            b64_str = data.split("_", 1)[1]
            b64_str += "=" * ((4 - len(b64_str) % 4) % 4)
            search_q = base64.urlsafe_b64decode(b64_str).decode()
        except: search_q = unquote(data.split("_", 1)[1])

        cursor = client.movies.find({"title": {"$regex": search_q, "$options": "i"}})
        results = await cursor.to_list(length=100)
        
        if not results:
            words = search_q.split()
            if words:
                keyword = max(words, key=len)
                cursor = client.movies.find({"title": {"$regex": keyword, "$options": "i"}})
                results = await cursor.to_list(length=100)

        if not results: return await msg.reply("❌ Files nahi mili!")

        sts = await msg.reply(f"🚀 **Found {len(results)} files.** Sending...")
        sent_messages = []
        for res in results:
            try:
                m = await client.send_cached_media(msg.chat.id, res["file_id"], caption=f"📂 `{res['title']}`\n\n⚠️ **5 min delete notice**")
                sent_messages.append(m)
                await asyncio.sleep(1.2)
            except: pass
        await sts.edit(f"✅ **Batch Complete!**")
        asyncio.create_task(delete_after_delay(sent_messages + [sts], 300))

# ================= ADMIN COMMANDS =================

@app.on_message(filters.command("shortlink") & filters.user(ADMIN_IDS))
async def toggle_shortlink_cmd(client, msg):
    global SHORTLINK_ENABLED
    choice = msg.command[1].lower() if len(msg.command) > 1 else ""
    if choice == "on": SHORTLINK_ENABLED = True; await msg.reply("✅ ON")
    elif choice == "off": SHORTLINK_ENABLED = False; await msg.reply("❌ OFF")

@app.on_message(filters.command("pratap") & filters.user(ADMIN_IDS))
async def stats_cmd(client, msg):
    count = await client.movies.count_documents({})
    await msg.reply(f"📊 Total: `{count}`")

@app.on_message(filters.chat(STORAGE_CHANNEL) & (filters.video | filters.document))
async def add_to_db(client, msg):
    file = msg.video or msg.document
    title = clean_name(msg.caption or file.file_name or "Unknown")
    await client.movies.insert_one({"title": title, "file_id": file.file_id})
    await msg.reply_text(f"✅ Added: {title}")

# ================= RUNNER =================

async def start_bot():
    app_web = web.Application()
    app_web.router.add_get("/", lambda r: web.Response(text="Bot Alive"))
    runner = web.AppRunner(app_web)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 8080))).start()
    await app.start()
    await idle()
    await app.stop()

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(start_bot())
