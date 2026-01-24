import os
import re
import asyncio
import aiohttp
import logging
import time
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client, filters, idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from pyrogram.errors import UserNotParticipant, FloodWait
from bson.objectid import ObjectId
from aiohttp import web

# ================= LOGGING =================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= CONFIG (VARS) =================
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URL = os.environ.get("MONGO_URL")

# IDs
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split()]
STORAGE_CHANNEL = int(os.environ.get("STORAGE_CHANNEL")) # Jahan file upload hogi DB ke liye
SEARCH_CHAT = int(os.environ.get("SEARCH_CHAT"))         # Jahan user search karega
FSUB_CHANNEL = int(os.environ.get("FSUB_CHANNEL"))       # Force Sub Channel ID

# Links & Keys
FSUB_LINK = os.environ.get("FSUB_LINK") # Channel ka Link
SEARCH_GROUP_LINK = os.environ.get("SEARCH_GROUP_LINK") # Search Group ka Link
TMDB_API_KEY = os.environ.get("TMDB_API_KEY") # TMDB Key (Zaroori hai Poster ke liye)

# Shortener Info
SHORT_DOMAIN = os.environ.get("SHORT_DOMAIN") # e.g. gplinks.com
SHORT_API_KEY = os.environ.get("SHORT_API_KEY")

# Settings
SHORTLINK_ENABLED = True # Default On rahega
USER_COOLDOWN = {} # Spam rokne ke liye

# ================= DATABASE =================
mongo = AsyncIOMotorClient(MONGO_URL)
db = mongo["PratapCinemaBot"]
movies_col = db["movies"]

# ================= WEB SERVER (RENDER) =================
async def health(_):
    return web.Response(text="Bhai Bot Ekdum Mast Chal Raha Hai!")

async def start_web():
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    await web.TCPSite(runner, "0.0.0.0", port).start()

# ================= BOT CLIENT =================
bot = Client(
    "pratap_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# ================= HELPER FUNCTIONS =================

def get_readable_time(seconds: int) -> str:
    count = 0
    ping_time = ""
    time_list = []
    time_suffix_list = ["s", "m", "h", "days"]
    while count < 4:
        count += 1
        remainder, result = divmod(seconds, 60) if count < 3 else divmod(seconds, 24)
        if seconds == 0 and remainder == 0:
            break
        time_list.append(int(result))
        seconds = int(remainder)
    for x in range(len(time_list)):
        time_list[x] = str(time_list[x]) + time_suffix_list[x]
    if len(time_list) == 4:
        ping_time += time_list.pop() + ", "
    time_list.reverse()
    ping_time += ":".join(time_list)
    return ping_time

# Text Clean karne ke liye
def clean_query(text):
    text = text.lower().strip()
    return text

# Shortener Logic
async def get_short_link(link):
    if not SHORTLINK_ENABLED or not SHORT_DOMAIN or not SHORT_API_KEY:
        return link
    
    url = f"https://{SHORT_DOMAIN}/api?api={SHORT_API_KEY}&url={link}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                data = await resp.json()
                return data.get("shortenedUrl", link)
    except Exception as e:
        logger.error(f"Shortener Error: {e}")
        return link

# TMDB se Poster aur Info nikalna
async def get_tmdb_data(query):
    if not TMDB_API_KEY:
        return None
    
    url = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={query}&language=en-US"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.json()
                if data['results']:
                    top = data['results'][0]
                    return {
                        "title": top.get("original_title", top.get("title")),
                        "year": top.get("release_date", "Unknown")[:4],
                        "rating": top.get("vote_average", 0),
                        "poster": f"https://image.tmdb.org/t/p/w500{top['poster_path']}" if top.get('poster_path') else None,
                        "overview": top.get("overview", "")[:100] + "..."
                    }
    except:
        pass
    return None

# ================= ADMIN COMMANDS =================

# 1. Check Total Movies (/pratapmovie)
@bot.on_message(filters.command("pratapmovie") & filters.user(ADMIN_IDS))
async def count_movies(_, msg):
    total = await movies_col.count_documents({})
    await msg.reply(f"📁 **Total Movies in DB:** {total}")

# 2. Delete Movie (/del movie_name)
@bot.on_message(filters.command("del") & filters.user(ADMIN_IDS))
async def delete_movie(_, msg):
    if len(msg.command) < 2:
        return await msg.reply("❌ Movie ka naam likho.\nExample: `/del Pathaan`")
    
    query = " ".join(msg.command[1:])
    result = await movies_col.delete_many({"title": {"$regex": query, "$options": "i"}})
    await msg.reply(f"🗑 **Deleted:** {result.deleted_count} files found for '{query}'.")

# 3. Shortener Toggle (/shortnr on/off)
@bot.on_message(filters.command("shortnr") & filters.user(ADMIN_IDS))
async def toggle_shortener(_, msg):
    global SHORTLINK_ENABLED
    if len(msg.command) < 2:
        return await msg.reply(f"Current Status: **{'ON' if SHORTLINK_ENABLED else 'OFF'}**\nUse: `/shortnr on` or `/shortnr off`")
    
    state = msg.command[1].lower()
    if state == "on":
        SHORTLINK_ENABLED = True
        await msg.reply("✅ **Shortener is now ON.**")
    elif state == "off":
        SHORTLINK_ENABLED = False
        await msg.reply("❌ **Shortener is now OFF.**")
    else:
        await msg.reply("❌ Galat command. Use 'on' or 'off'.")

# 4. Broadcast Poster (/postar caption - reply to photo)
@bot.on_message(filters.command("postar") & filters.user(ADMIN_IDS) & filters.reply)
async def broadcast_poster(_, msg):
    if not msg.reply_to_message.photo:
        return await msg.reply("❌ Kisi photo pe reply karke command do.")
    
    caption = msg.text.split(None, 1)[1] if len(msg.command) > 1 else msg.reply_to_message.caption
    
    # Yahan logic simple rakha hai, filhal bas confirmation de raha hu
    # Real broadcast ke liye database me users save hone chahiye
    await msg.reply(f"✅ Poster Broadcast command received. (Users DB not connected so just testing)\nCaption: {caption}")

# 5. Broadcast SMS (/sms text)
@bot.on_message(filters.command("sms") & filters.user(ADMIN_IDS))
async def broadcast_sms(_, msg):
    if len(msg.command) < 2:
        return await msg.reply("❌ Message likho. Example: `/sms Hello Users`")
    
    text = msg.text.split(None, 1)[1]
    await msg.reply(f"✅ SMS Broadcast command received.\nText: {text}")

# ================= FILE INDEXING (AUTO) =================

@bot.on_message(filters.chat(STORAGE_CHANNEL) & (filters.document | filters.video))
async def index_file(_, msg):
    media = msg.document or msg.video
    name = media.file_name or msg.caption or "Unknown Movie"
    name = name.replace(".", " ").replace("_", " ") # Basic cleaning
    
    # Save to DB
    await movies_col.insert_one({
        "title": name,
        "file_id": media.file_id,
        "caption": msg.caption
    })
    # Admin ko pata chale
    await msg.reply(f"✅ **Saved:** `{name}`")

# ================= SEARCH LOGIC (GROUP ONLY) =================

@bot.on_message(filters.chat(SEARCH_CHAT) & filters.text & ~filters.command)
async def search_movie(client, msg):
    query = msg.text
    user_id = msg.from_user.id
    
    # 1. Cooldown Check (Spam rokne ke liye)
    current_time = time.time()
    if user_id in USER_COOLDOWN:
        if current_time - USER_COOLDOWN[user_id] < 10: # 10 Seconds wait
            return await msg.reply(f"✋ **Wait karo bhai!** Thoda saans lo.\nAgla search 5-6 second baad karna.", quote=True)
    
    USER_COOLDOWN[user_id] = current_time

    # 2. Search in DB
    # Regex search (case insensitive)
    movie = await movies_col.find_one({"title": {"$regex": query, "$options": "i"}})
    
    if not movie:
        temp = await msg.reply(f"❌ **'{query}'** movie database me nahi mili.\nSpelling check karo.", quote=True)
        await asyncio.sleep(10)
        await temp.delete()
        await msg.delete()
        return

    # 3. Get TMDB Data
    tmdb_info = await get_tmdb_data(movie['title'])
    
    poster_url = tmdb_info['poster'] if tmdb_info else "https://telegra.ph/file/5a5a90d40523292453c24.jpg" # Default image
    rating = tmdb_info['rating'] if tmdb_info else "N/A"
    year = tmdb_info['year'] if tmdb_info else "N/A"
    
    # 4. Generate Link
    # Bot start link with File ID
    long_url = f"https://t.me/{client.me.username}?start=file_{movie['_id']}"
    
    # Shorten if Enabled
    final_link = await get_short_link(long_url)

    # 5. Caption Make
    caption = (
        f"🎬 **Title:** {movie['title']}\n"
        f"⭐️ **Rating:** {rating}/10\n"
        f"📅 **Year:** {year}\n\n"
        f"👤 **User:** {msg.from_user.mention} (`{user_id}`)\n\n"
        f"👇 **Movie Download Ke Liye Niche Dabaye** 👇"
    )

    btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 GET MOVIE LINK 📂", url=final_link)]
    ])

    # 6. Send Result
    if poster_url:
        sent_msg = await msg.reply_photo(photo=poster_url, caption=caption, reply_markup=btn)
    else:
        sent_msg = await msg.reply(caption, reply_markup=btn)

    # 7. Auto Delete Group Msg (1 Minute)
    await asyncio.sleep(60)
    try:
        await sent_msg.delete()
        await msg.delete() # User ka message bhi delete
    except:
        pass

# ================= PRIVATE CHAT (BLOCK SEARCH & SEND FILE) =================

@bot.on_message(filters.private & filters.text)
async def private_handler(client, msg):
    
    # 1. Handle /start command with File ID
    if msg.text.startswith("/start file_"):
        
        # Check Force Subscribe
        try:
            await client.get_chat_member(FSUB_CHANNEL, msg.from_user.id)
        except UserNotParticipant:
            invite_link = FSUB_LINK
            return await msg.reply(
                "⚠️ **Bhai Pehle Channel Join Karo!**\nTabhi movie file milegi.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔔 JOIN CHANNEL", url=invite_link)],
                    [InlineKeyboardButton("🔄 TRY AGAIN", url=msg.text)] # Same start link
                ])
            )
        except Exception as e:
            print(f"FSUB Error: {e}")

        # Extract File ID
        try:
            file_oid = msg.text.split("_")[1]
            movie_data = await movies_col.find_one({"_id": ObjectId(file_oid)})
        except:
            return await msg.reply("❌ Invalid Link.")

        if not movie_data:
            return await msg.reply("❌ Movie file delete ho chuki hai.")

        # Send File
        caption_text = (
            f"👁️ 2 [Movies 2026 - Cinema Pratap ❤️🌹]\n\n"
            f"🎥 **{movie_data['title']}**\n\n"
            f"⚠️ **Ye file 2 minute me auto delete ho jayegi!**\n"
            f"Isliye jaldi se Forward ya Save kar lo."
        )

        sent_file = await client.send_cached_media(
            chat_id=msg.chat.id,
            file_id=movie_data['file_id'],
            caption=caption_text
        )

        # Auto Delete File (2 Minutes)
        await asyncio.sleep(120)
        try:
            await sent_file.delete()
            await msg.reply("🔥 **File 2 Minute ho gaye isliye delete kar di gayi.**")
        except:
            pass
        return

    # 2. Block Direct Search in PM (Agar normal text bheja)
    if not msg.text.startswith("/"):
        btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔎 SEARCH HERE", url=SEARCH_GROUP_LINK)]
        ])
        await msg.reply(
            "🙏 **Bhai is bot par search nahi hota!**\n\n"
            "Movie search karne ke liye niche button dabao aur Group me jao. 👇",
            reply_markup=btn
        )

# ================= RUN BOT =================
if __name__ == "__main__":
    print("Bot Starting...")
    loop = asyncio.get_event_loop()
    loop.run_until_complete(start_web())
    bot.run()
