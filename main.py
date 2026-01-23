import os
import re
import asyncio
import aiohttp
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client, filters, idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import UserNotParticipant
from bson.objectid import ObjectId
from aiohttp import web

# ================= CONFIG =================

# Environment Variables check kar lena Render me sahi se dale ho
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URL = os.environ.get("MONGO_URL")
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")

STORAGE_CHANNEL = int(os.environ.get("STORAGE_CHANNEL"))
SEARCH_CHAT = int(os.environ.get("SEARCH_CHAT"))
FSUB_CHANNEL = int(os.environ.get("FSUB_CHANNEL"))
MAIN_CHANNEL_LINK = os.environ.get("MAIN_CHANNEL_LINK")

SHORT_DOMAIN = os.environ.get("SHORT_DOMAIN")
SHORT_API_KEY = os.environ.get("SHORT_API_KEY")

raw_admins = os.environ.get("ADMIN_IDS", "")
ADMIN_IDS = [int(x) for x in re.findall(r'-?\d+', raw_admins)]

SHORTLINK_ENABLED = True

# ================= DB CONNECTION =================

print("Connecting to MongoDB...")
try:
    mongo = AsyncIOMotorClient(MONGO_URL)
    db = mongo["PratapCinemaBot"]
    movies = db["movies"]
    print("✅ MongoDB Connected Successfully")
except Exception as e:
    print(f"❌ MongoDB Connection Failed: {e}")

# ================= SERVER =================

async def health(request):
    return web.Response(text="Bot is alive")

async def start_web():
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

# ================= BOT CLIENT =================

bot = Client("pratap_session", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ================= FUNCTIONS =================

def clean_name(text):
    if not text:
        return ""
    text = text.lower()
    junk = [r'1080p', r'720p', '480p', 'x264', 'x265', 'hevc', 'hindi', 'english', r'\.']
    for j in junk:
        text = re.sub(j, '', text)
    return " ".join(text.replace('.', ' ').replace('_', ' ').split()).strip()

async def get_tmdb(query):
    if not TMDB_API_KEY:
        return None, "N/A", "0000"
    url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={query}"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=6) as r:
                data = await r.json()
                if data.get('results'):
                    res = data['results'][0]
                    p_path = res.get("backdrop_path") or res.get("poster_path")
                    poster = f"https://image.tmdb.org/t/p/w500{p_path}" if p_path else None
                    rating = res.get('vote_average', 'N/A')
                    year = (res.get('release_date') or res.get('first_air_date') or '0000')[:4]
                    return poster, rating, year
    except:
        pass
    return None, "N/A", "0000"

async def shortlink(url):
    global SHORTLINK_ENABLED
    if not SHORTLINK_ENABLED:
        return url
    try:
        api = f"https://{SHORT_DOMAIN}/api?api={SHORT_API_KEY}&url={url}"
        async with aiohttp.ClientSession() as s:
            async with s.get(api, timeout=10) as r:
                res = await r.json()
                if res.get("status") == "success":
                    return res.get("shortenedUrl")
    except:
        pass
    return url

async def auto_delete(msg, t=120):
    await asyncio.sleep(t)
    try:
        await msg.delete()
    except:
        pass

# ================= LOGIC =================

# 1. MOVIE ADD (Storage Channel)
@bot.on_message(filters.chat(STORAGE_CHANNEL) & (filters.video | filters.document))
async def add_movie(client, msg):
    file = msg.video or msg.document
    if not file:
        return
    
    # Filename clean karo
    raw_name = msg.caption or file.file_name or "unknown"
    title = clean_name(raw_name)
    
    # DB me daalo
    try:
        await movies.insert_one({
            "title": title,
            "file_id": file.file_id,
            "caption": msg.caption
        })
        await msg.reply_text(f"✅ **Added:** `{title}`")
    except Exception as e:
        print(f"Error adding movie: {e}")

# 2. MOVIE SEARCH (Group)
@bot.on_message(filters.chat(SEARCH_CHAT) & filters.text)
async def search(client, msg):
    if msg.text.startswith("/"):
        return
    
    query = clean_name(msg.text)
    if len(query) < 3:
        return

    # DB Search
    res = await movies.find_one({"title": {"$regex": query, "$options": "i"}})
    
    if not res:
        # Not found message
        m = await msg.reply(f"❌ `{msg.text}` movie nahi mili.")
        asyncio.create_task(auto_delete(m, 10))
        return

    # Found - Process Link
    poster, rating, year = await get_tmdb(query)
    me = await bot.get_me()
    
    # Start link banao
    bot_link = f"https://t.me/{me.username}?start=file_{str(res['_id'])}"
    final_link = await shortlink(bot_link)

    btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 GET MOVIE", url=final_link)],
        [InlineKeyboardButton("📢 JOIN CHANNEL", url=MAIN_CHANNEL_LINK)]
    ])

    cap = (f"🎬 **Title:** `{res['title']}`\n"
           f"⭐ Rating: `{rating}` | 📅 Year: `{year}`\n\n"
           f"👤 Requested by: {msg.from_user.mention}")

    if poster:
        sent = await msg.reply_photo(poster, caption=cap, reply_markup=btn)
    else:
        sent = await msg.reply(cap, reply_markup=btn)

    asyncio.create_task(auto_delete(sent, 120))

# ================= ADMIN COMMANDS =================

# Command changed to /pratap as per your screenshot
@bot.on_message(filters.command(["pratap", "stats"]) & filters.user(ADMIN_IDS))
async def stats(client, msg):
    c = await movies.count_documents({})
    await msg.reply(f"📊 **Total Movies:** {c}\n✅ Bot is Running!")

@bot.on_message(filters.command("shortnr") & filters.user(ADMIN_IDS))
async def toggle_short(client, msg):
    global SHORTLINK_ENABLED
    if len(msg.command) < 2:
        return await msg.reply("Use: `/shortnr on` or `/shortnr off`")
    
    state = msg.command[1].lower()
    if state == "on":
        SHORTLINK_ENABLED = True
        await msg.reply("✅ Shortlink Enabled")
    elif state == "off":
        SHORTLINK_ENABLED = False
        await msg.reply("❌ Shortlink Disabled")

@bot.on_message(filters.command("del") & filters.user(ADMIN_IDS))
async def delete_movie(client, msg):
    if len(msg.command) < 2:
        return await msg.reply("Use: `/del movie_name`")
    
    q = " ".join(msg.command[1:])
    r = await movies.delete_many({"title": {"$regex": q, "$options": "i"}})
    await msg.reply(f"🗑 **Deleted:** {r.deleted_count} movies")

# ================= START & FILE DELIVERY =================

@bot.on_message(filters.command("start") & filters.private)
async def start(client, msg):
    # FSUB Check
    try:
        await client.get_chat_member(FSUB_CHANNEL, msg.from_user.id)
    except UserNotParticipant:
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("📢 JOIN CHANNEL FIRST", url=MAIN_CHANNEL_LINK)]])
        return await msg.reply("❌ **Pehle Channel Join Karo!**", reply_markup=btn)
    except Exception:
        pass # Admin ya owner ho sakta hai

    # File Delivery
    if len(msg.command) > 1 and msg.command[1].startswith("file_"):
        mid = msg.command[1].replace("file_", "")
        try:
            res = await movies.find_one({"_id": ObjectId(mid)})
            if res:
                sent = await client.send_cached_media(
                    chat_id=msg.chat.id, 
                    file_id=res["file_id"], 
                    caption=f"🎥 **{res['title']}**\n\n⚠️ *Auto delete in 2 mins*"
                )
                asyncio.create_task(auto_delete(sent, 120))
            else:
                await msg.reply("❌ Movie database se delete ho gayi hai.")
        except:
            await msg.reply("❌ Error fetching file.")
    else:
        await msg.reply(f"👋 Hello {msg.from_user.mention}!\nMovie search karne ke liye Group me naam likho.")

# ================= RUNNER =================

async def main():
    await start_web()
    print("🤖 Bot Starting...")
    await bot.start()
    print("🚀 Bot Started Successfully!")
    await idle()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
