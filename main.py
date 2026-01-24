import os
import re
import asyncio
import aiohttp
import logging
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client, filters, idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import UserNotParticipant
from bson.objectid import ObjectId
from aiohttp import web

# ================= LOGGING =================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= CONFIG =================
# 1. Telegram API (Render se automatic uthayega)
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# 2. Database (Render se uthayega)
MONGO_URL = os.environ.get("MONGO_URL", "")

# 3. TMDB Key (Poster ke liye)
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")

# 4. Channels & Groups
# Yahan try-except lagaya hai taki agar Render pe ID na mile to bot crash na ho
try:
    STORAGE_CHANNEL = int(os.environ.get("STORAGE_CHANNEL", 0)) 
    SEARCH_CHAT = int(os.environ.get("SEARCH_CHAT", 0)) 
    FSUB_CHANNEL = int(os.environ.get("FSUB_CHANNEL", 0)) 
except:
    STORAGE_CHANNEL = 0
    SEARCH_CHAT = 0
    FSUB_CHANNEL = 0

# 5. Links
MAIN_CHANNEL_LINK = os.environ.get("MAIN_CHANNEL_LINK", "") 
SEARCH_GROUP_LINK = os.environ.get("SEARCH_GROUP_LINK", "")

# 6. Shortener
SHORT_DOMAIN = os.environ.get("SHORT_DOMAIN", "")
SHORT_API_KEY = os.environ.get("SHORT_API_KEY", "")

# 7. ADMIN IDS (CRITICAL FIX)
# Maine apki ID '6429831771' yahan fix kar di hai.
# Ab bot apko Admin manega hi manega.
FIXED_ADMIN = 6429831771 
ADMIN_IDS = [FIXED_ADMIN] 

# Render se baaki admins bhi load karlo
try:
    env_admins = [int(x) for x in str(os.environ.get("ADMIN_IDS", "")).replace(',', ' ').split() if x.strip()]
    ADMIN_IDS.extend(env_admins)
    ADMIN_IDS = list(set(ADMIN_IDS)) # Duplicate hata do
except:
    pass

logger.info(f"Loaded Admins: {ADMIN_IDS}") # Logs me dikhega kon admin hai

SHORTLINK_ENABLED = True

# ===== WATERMARK TEXT =====
WATERMARK_TEXT = (
    "👁️ 2  [Movies 2026 - Cinema Pratap ❤️🌹]\n\n"
    "⚠️ Ye file 2 minute me auto delete ho jayegi!"
)

# ================= DB CONNECTION =================
mongo = AsyncIOMotorClient(MONGO_URL)
db = mongo["PratapCinemaBot"]
movies = db["movies"]

# ================= TMDB HELPER =================
async def get_tmdb_details(query):
    if not TMDB_API_KEY:
        return None, "N/A", "N/A"
    try:
        url = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={query}&language=en-US"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    if data['results']:
                        movie = data['results'][0]
                        poster = f"https://image.tmdb.org/t/p/w500{movie.get('poster_path')}" if movie.get('poster_path') else None
                        rating = movie.get('vote_average', 0)
                        year = movie.get('release_date', 'N/A').split("-")[0]
                        return poster, rating, year
    except:
        pass
    return None, "N/A", "N/A"

# ================= WEB SERVER =================
async def health(request):
    return web.Response(text="Bot is Live and Running!")

async def start_web():
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    await web.TCPSite(runner, "0.0.0.0", port).start()

# ================= BOT CLIENT =================
bot = Client(
    "pratap_session",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True
)

# ================= HELPERS =================
def clean_name(text):
    if not text: return "unknown"
    text = text.lower()
    junk = ['1080p','720p','480p','x264','x265','hevc','hindi','english', 'dual audio', 'web-dl']
    for j in junk: text = text.replace(j, '')
    return " ".join(text.replace('.', ' ').replace('_',' ').replace('-', ' ').split())

async def shortlink(url):
    if not SHORTLINK_ENABLED or not SHORT_DOMAIN: return url
    try:
        api = f"https://{SHORT_DOMAIN}/api?api={SHORT_API_KEY}&url={url}"
        async with aiohttp.ClientSession() as s:
            async with s.get(api, timeout=10) as r:
                if r.status == 200:
                    j = await r.json()
                    return j.get("shortenedUrl", url)
    except: pass
    return url

async def auto_delete(msg, t=60):
    await asyncio.sleep(t)
    try: await msg.delete()
    except: pass

# ================= 1. AUTO ADD MOVIE =================
@bot.on_message(filters.chat(STORAGE_CHANNEL) & (filters.video | filters.document))
async def add_movie(_, msg):
    file = msg.video or msg.document
    original_caption = msg.caption or file.file_name or "unknown"
    title = clean_name(original_caption)
    
    exist = await movies.find_one({"file_id": file.file_id})
    if exist:
        sent = await msg.reply(f"❌ **{title}** pehle se added hai!")
        asyncio.create_task(auto_delete(sent, 60))
        return

    await movies.insert_one({"title": title, "file_id": file.file_id, "caption": original_caption})
    sent = await msg.reply(f"✅ **Movie Added:**\n`{title}`")
    asyncio.create_task(auto_delete(sent, 60))

# ================= 2. DEBUG COMMAND (TESTING) =================
# Ye command sabke liye chalegi, check karne ke liye ki bot zinda hai
@bot.on_message(filters.command("check"))
async def check_alive(_, msg):
    await msg.reply(f"✅ Bot is Alive!\nYour ID: `{msg.from_user.id}`\nChat ID: `{msg.chat.id}`")

# ================= 3. ADMIN COMMANDS =================
@bot.on_message(filters.command("pratap") & filters.user(ADMIN_IDS))
async def stats(_, msg):
    c = await movies.count_documents({})
    await msg.reply(f"📊 **Database Status:**\nTotal Movies: {c}\n\nAdmin ID Verified: ✅")

@bot.on_message(filters.command("del") & filters.user(ADMIN_IDS))
async def delete_movie(_, msg):
    if len(msg.command) < 2: return await msg.reply("❌ Name likho.")
    q = " ".join(msg.command[1:])
    r = await movies.delete_many({"title": {"$regex": q, "$options": "i"}})
    await msg.reply(f"🗑 **Deleted:** {r.deleted_count}")

@bot.on_message(filters.command("shortn") & filters.user(ADMIN_IDS))
async def toggle_shortener(_, msg):
    global SHORTLINK_ENABLED
    if len(msg.command) < 2: return await msg.reply("on/off likho.")
    state = msg.command[1].lower()
    SHORTLINK_ENABLED = (state == "on")
    await msg.reply(f"Shortener: {state.upper()}")

@bot.on_message(filters.command("postar") & filters.user(ADMIN_IDS))
async def postar_cmd(client, msg):
    # Agar FSUB_CHANNEL 0 hai, to error mat do, bas bata do
    if not FSUB_CHANNEL:
        return await msg.reply("❌ FSUB_CHANNEL ID set nahi hai Render me.")
        
    target = FSUB_CHANNEL
    reply = msg.reply_to_message
    if not reply: return await msg.reply("Reply karke likho.")
    
    btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔍 SEARCH HERE", url=SEARCH_GROUP_LINK)]])
    
    try:
        custom = " ".join(msg.command[1:])
        if reply.photo:
            cap = custom if custom else (reply.caption or "Movie")
            await client.send_photo(target, reply.photo.file_id, caption=cap, reply_markup=btn)
        else:
            txt = custom if custom else (reply.text or "Update")
            await client.send_message(target, txt, reply_markup=btn)
        await msg.reply("✅ Posted!")
    except Exception as e:
        await msg.reply(f"❌ Error: {e}")

# ================= 4. GROUP SEARCH =================
@bot.on_message(filters.chat(SEARCH_CHAT) & filters.text & ~filters.regex(r"^/"))
async def group_search(client, msg):
    query = clean_name(msg.text)
    if len(query) < 2: return

    res = await movies.find_one({"title": {"$regex": query, "$options": "i"}})
    if not res:
        temp = await msg.reply(f"😞 **{msg.text}** nahi mili.")
        asyncio.create_task(auto_delete(temp, 20))
        return

    poster, rating, year = await get_tmdb_details(res['title'])
    
    me = await client.get_me()
    link = f"https://t.me/{me.username}?start=file_{res['_id']}"
    final_link = await shortlink(link)

    btn = InlineKeyboardMarkup([[InlineKeyboardButton("📂 DOWNLOAD MOVIE 📂", url=final_link)]])
    
    caption = (
        f"🎬 **{res['title'].title()}**\n"
        f"📅 **Year:** {year} | ⭐ **Rating:** {rating}\n"
        f"👤 **User:** {msg.from_user.mention} (`{msg.from_user.id}`)\n\n"
        f"👇 **Download Link:**"
    )

    if poster:
        await msg.reply_photo(poster, caption=caption, reply_markup=btn)
    else:
        await msg.reply(caption, reply_markup=btn)

# ================= 5. START & REDIRECT =================
@bot.on_message(filters.private & filters.command("start"))
async def start(_, msg):
    if FSUB_CHANNEL:
        try:
            await bot.get_chat_member(FSUB_CHANNEL, msg.from_user.id)
        except UserNotParticipant:
            start_arg = msg.command[1] if len(msg.command) > 1 else ""
            return await msg.reply(
                "⚠️ **Channel Join Karo!**",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("JOIN CHANNEL", url=MAIN_CHANNEL_LINK)],
                    [InlineKeyboardButton("TRY AGAIN", url=f"https://t.me/{bot.me.username}?start={start_arg}")]
                ])
            )
        except: pass

    if len(msg.command) > 1 and msg.command[1].startswith("file_"):
        try:
            mid = msg.command[1].replace("file_", "")
            res = await movies.find_one({"_id": ObjectId(mid)})
            if res:
                sent = await bot.send_cached_media(msg.chat.id, res["file_id"], caption=WATERMARK_TEXT)
                asyncio.create_task(auto_delete(sent, 120))
                return
        except: pass
        await msg.reply("❌ File nahi mili.")
    else:
        await msg.reply("👋 Hello! Search group me jao.")

@bot.on_message(filters.private & filters.text & ~filters.command(["start", "pratap", "del", "shortn", "postar", "check"]))
async def redirect_user(_, msg):
    await msg.reply(
        "🙏 **Bhai yahan search mat karo.**\nSearch Group me jao:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔍 GO TO GROUP", url=SEARCH_GROUP_LINK)]])
    )

# ================= MAIN =================
async def main():
    await start_web()
    logger.info("Bot Starting...")
    await bot.start()
    logger.info("Bot Started!")
    await idle()
    await bot.stop()

if __name__ == "__main__":
    asyncio.run(main())
