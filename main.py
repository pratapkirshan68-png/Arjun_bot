import os
import re
import asyncio
import aiohttp
import logging
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import UserNotParticipant
from motor.motor_asyncio import AsyncIOMotorClient
from bson.objectid import ObjectId
from aiohttp import web

# ================= CONFIGURATION (Environment Variables) =================
# Ye sab Render ki settings me dalna hai, code me mat likhna
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
MONGO_URL = os.environ.get("MONGO_URL", "") # MongoDB Connection String

# Admin & Channels
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split()]
STORAGE_CHANNEL = int(os.environ.get("STORAGE_CHANNEL", "0")) # Jahan file upload karoge
LOG_CHANNEL = int(os.environ.get("LOG_CHANNEL", "0")) # Jahan "Movie Added" ka SMS ayega
SEARCH_CHAT = int(os.environ.get("SEARCH_CHAT", "0")) # Group ID
FSUB_CHANNEL = int(os.environ.get("FSUB_CHANNEL", "0")) # Force Subscribe Channel
MAIN_CHANNEL_LINK = os.environ.get("MAIN_CHANNEL_LINK", "https://t.me/YourChannel")

# APIs
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")
SHORT_DOMAIN = os.environ.get("SHORT_DOMAIN", "arolinks.com")
SHORT_API_KEY = os.environ.get("SHORT_API_KEY", "")

# Settings
SHORTLINK_ENABLED = True 

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MovieBot(Client):
    def __init__(self):
        super().__init__("pratap_session", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
        self.mongo_client = None
        self.db = None
        self.movies = None

    async def start(self):
        await super().start()
        # MongoDB Connection
        self.mongo_client = AsyncIOMotorClient(MONGO_URL)
        self.db = self.mongo_client["PratapCinemaBot"]
        self.movies = self.db["movies"]
        
        self.bot_info = await self.get_me()
        print(f"🚀 BOT @{self.bot_info.username} STARTED WITH MONGODB")

    async def stop(self, *args):
        await super().stop()
        if self.mongo_client:
            self.mongo_client.close()

app = MovieBot()

# ================== WEB SERVER (Keep Alive for Render) ==================
async def health_check(request):
    return web.Response(text="Bot is Alive & Running")

async def start_web_server():
    server = web.Application()
    server.router.add_get("/", health_check)
    runner = web.AppRunner(server)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    await web.TCPSite(runner, "0.0.0.0", port).start()

# ================= HELPERS =================

async def get_shortlink(url):
    global SHORTLINK_ENABLED
    if not SHORTLINK_ENABLED: 
        return url
    try:
        api_url = f"https://{SHORT_DOMAIN}/api?api={SHORT_API_KEY}&url={url}"
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=10) as resp:
                res = await resp.json()
                if res.get("status") == "success": 
                    return res["shortenedUrl"]
    except Exception as e:
        logger.error(f"Shortlink Error: {e}")
    return url

def clean_name(text):
    if not text: return ""
    text = text.lower()
    junk = [r'\(.*?\)', r'\[.*?\]', '1080p', '720p', '480p', 'x264', 'x265', 'hevc', 'hindi', 'english', 'dual audio', 'web-dl', 'bluray']
    for word in junk: text = re.sub(word, '', text)
    return " ".join(text.replace(".", " ").replace("_", " ").split()).strip()

async def get_tmdb_info(query):
    search_q = re.sub(r'\d{4}', '', query).strip()
    url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={search_q}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('results'):
                        res = data['results'][0]
                        p_path = res.get('backdrop_path') or res.get('poster_path')
                        poster = f"https://image.tmdb.org/t/p/w780{p_path}" if p_path else None
                        title = res.get('title') or res.get('name') or query.upper()
                        rating = res.get('vote_average', 'N/A')
                        year = (res.get('release_date') or res.get('first_air_date') or "0000")[:4]
                        return poster, title, rating, year
    except: pass
    return None, query.upper(), "N/A", "0000"

async def delete_after_delay(msgs, delay):
    await asyncio.sleep(delay)
    for m in msgs:
        try: await m.delete()
        except: pass

# ================= ADMIN COMMANDS =================

@app.on_message(filters.command("pratap") & filters.user(ADMIN_IDS))
async def stats_cmd(client, msg):
    count = await client.movies.count_documents({})
    await msg.reply(f"📊 **Total Movies in MongoDB:** `{count}`")

@app.on_message(filters.command("shortlink") & filters.user(ADMIN_IDS))
async def toggle_shortlink_cmd(client, msg):
    global SHORTLINK_ENABLED
    if len(msg.command) < 2:
        return await msg.reply("Usage: `/shortlink on` or `/shortlink off`")
    
    choice = msg.command[1].lower()
    if choice == "on":
        SHORTLINK_ENABLED = True
        await msg.reply("✅ Shortlink has been **ENABLED**.")
    elif choice == "off":
        SHORTLINK_ENABLED = False
        await msg.reply("❌ Shortlink has been **DISABLED**.")

@app.on_message(filters.command("del") & filters.user(ADMIN_IDS))
async def delete_movie_cmd(client, msg):
    if len(msg.command) < 2:
        return await msg.reply("Usage: `/del movie_name`")
    query = " ".join(msg.command[1:])
    # MongoDB Delete
    result = await client.movies.delete_many({"title": {"$regex": query, "$options": "i"}})
    await msg.reply(f"🗑️ `{result.deleted_count}` movies removed matching `{query}`.")

# ================= STORAGE INDEXING & NOTIFICATION =================

@app.on_message(filters.chat(STORAGE_CHANNEL) & (filters.video | filters.document))
async def add_to_db(client, msg):
    file = msg.video or msg.document
    if not file: return

    # Original clean name logic
    title = clean_name(msg.caption or file.file_name or "Unknown")
    
    # MongoDB Insert
    movie_data = {
        "title": title,
        "file_id": file.file_id,
        "caption": msg.caption or title
    }
    await client.movies.insert_one(movie_data)
    
    # Confirmation in Storage Channel
    await msg.reply_text(f"✅ **Movie Added to MongoDB:** `{title}`")
    
    # SMS TO CHANNEL (Notification Feature)
    try:
        if LOG_CHANNEL:
            await client.send_message(
                LOG_CHANNEL,
                f"🎬 **New Movie Added!**\n\n"
                f"📛 **Name:** `{title}`\n"
                f"✅ **Status:** Uploaded to Database\n"
                f"🤖 **Bot:** @{client.bot_info.username}"
            )
    except Exception as e:
        print(f"Notification Error: {e}")

# ================= SEARCH LOGIC =================

@app.on_message(filters.chat(SEARCH_CHAT) & filters.text & ~filters.command(["start", "pratap", "shortlink", "del"]))
async def search_movie(client, msg):
    query = clean_name(msg.text)
    if len(query) < 3: return

    try: await msg.delete()
    except: pass

    u_name = msg.from_user.first_name if msg.from_user else "User"
    u_id = msg.from_user.id if msg.from_user else "N/A"

    sm = await client.send_message(msg.chat.id, f"🔍 **Searching:** `{msg.text}`...")

    # MongoDB Search (Regex for pattern matching)
    res = await client.movies.find_one({"title": {"$regex": query, "$options": "i"}})

    if not res:
        await sm.edit(f"❌ `{msg.text}` nahi mili! Spelling check karein.")
        asyncio.create_task(delete_after_delay([sm], 15))
        return 

    db_id = str(res["_id"]) # Get MongoDB Object ID
    db_title = res["title"]
    
    poster, m_title, m_rating, m_year = await get_tmdb_info(query)

    bot_url = f"https://t.me/{client.bot_info.username}?start=file_{db_id}"
    final_link = await get_shortlink(bot_url)

    btn = InlineKeyboardMarkup([[InlineKeyboardButton("🚀 DOWNLOAD / WATCH NOW", url=final_link)],
                                [InlineKeyboardButton("✨ JOIN CHANNEL ✨", url=MAIN_CHANNEL_LINK)]])

    cap = (f"✅ **Movie Mil Gayi!**\n\n🎬 **Naam:** `{db_title}`\n"
           f"🌟 **Rating:** `{m_rating}` | 📅 **Year:** `{m_year}`\n\n"
           f"👤 **User:** {u_name}\n🆔 **ID:** `{u_id}`\n\n"
           f"👁️ 2  [Movies 2026 - Cinema Pratap\"❤️🌹]({MAIN_CHANNEL_LINK})")

    if poster:
        res_msg = await client.send_photo(msg.chat.id, poster, caption=cap, reply_markup=btn)
    else:
        res_msg = await client.send_message(msg.chat.id, cap, reply_markup=btn)
    
    await sm.delete()
    asyncio.create_task(delete_after_delay([res_msg], 120))

# ================= START / FSUB =================

@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, msg):
    user_id = msg.from_user.id
    
    # PEHLE JOIN CHECK
    try:
        await client.get_chat_member(FSUB_CHANNEL, user_id)
    except UserNotParticipant:
        try:
            invite = (await client.get_chat(FSUB_CHANNEL)).invite_link
        except:
            invite = MAIN_CHANNEL_LINK # Fallback
            
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("📢 JOIN CHANNEL FIRST 📢", url=invite)],
                                    [InlineKeyboardButton("✅ TRY AGAIN", url=f"https://t.me/{client.bot_info.username}?start={msg.command[1] if len(msg.command)>1 else ''}")]])
        return await msg.reply("❌ **Access Denied!**\n\nFile paane ke liye pehle niche diye gaye channel ko join karein.", reply_markup=btn)
    except Exception as e:
        print(f"Join Check Error: {e}")

    # AGAR JOIN HAI
    if len(msg.command) < 2:
        return await msg.reply("👋 Namaste! Group me movie search karein.")

    data = msg.command[1]
    if data.startswith("file_"):
        m_id = data.split("_")[1]
        
        try:
            # MongoDB Fetch by ObjectId
            res = await client.movies.find_one({"_id": ObjectId(m_id)})
        except:
            return await msg.reply("❌ Invalid Link or File Removed.")
        
        if res:
            f_id = res["file_id"]
            title = res["title"]
            caption = (f"📂 **File Name:** `{title}`\n👤 **Admin:** pratap 🇮🇳❤️\n\n"
                       f"🚀 **Channel:** {MAIN_CHANNEL_LINK}\n\n"
                       f"👁️ 2  [Movies 2026 - Cinema Pratap\"❤️🌹]({MAIN_CHANNEL_LINK})\n\n"
                       f"⚠️ **Warning:** 2 minute me delete ho jayegi!")
            
            sf = await client.send_cached_media(msg.chat.id, f_id, caption=caption)
            asyncio.create_task(delete_after_delay([sf], 120))
        else:
            await msg.reply("❌ File Not Found in Database.")

if __name__ == "__main__":
    # Start Web Server and Bot together
    loop = asyncio.get_event_loop()
    loop.create_task(start_web_server())
    app.run()    if not text: return ""
    text = text.lower()
    junk = [r'\(.*?\)', r'\[.*?\]', '1080p', '720p', '480p', 'x264', 'x265', 'hevc', 'hindi', 'english']
    for word in junk: text = re.sub(word, '', text)
    return " ".join(text.replace(".", " ").replace("_", " ").split()).strip()

async def get_tmdb_info(query):
    search_q = re.sub(r'\d{4}', '', query).strip()
    url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={search_q}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('results'):
                        res = data['results'][0]
                        p_path = res.get('backdrop_path') or res.get('poster_path')
                        poster = f"https://image.tmdb.org/t/p/w780{p_path}" if p_path else None
                        title = res.get('title') or res.get('name') or query.upper()
                        rating = res.get('vote_average', 'N/A')
                        year = (res.get('release_date') or res.get('first_air_date') or "0000")[:4]
                        return poster, title, rating, year
    except: pass
    return None, query.upper(), "N/A", "0000"

async def delete_after_delay(msgs, delay):
    await asyncio.sleep(delay)
    for m in msgs:
        try: await m.delete()
        except: pass

# ================= ADMIN COMMANDS =================

@app.on_message(filters.command("pratap") & filters.user(ADMIN_IDS))
async def stats_cmd(client, msg):
    cursor = await client.db.execute("SELECT COUNT(*) FROM movies")
    count = (await cursor.fetchone())[0]
    await msg.reply(f"📊 **Total Movies in DB:** `{count}`")

@app.on_message(filters.command("shortlink") & filters.user(ADMIN_IDS))
async def toggle_shortlink_cmd(client, msg):
    global SHORTLINK_ENABLED
    if len(msg.command) < 2:
        return await msg.reply("Usage: `/shortlink on` or `/shortlink off`")
    
    choice = msg.command[1].lower()
    if choice == "on":
        SHORTLINK_ENABLED = True
        await msg.reply("✅ Shortlink has been **ENABLED**.")
    elif choice == "off":
        SHORTLINK_ENABLED = False
        await msg.reply("❌ Shortlink has been **DISABLED**.")

@app.on_message(filters.command("del") & filters.user(ADMIN_IDS))
async def delete_movie_cmd(client, msg):
    if len(msg.command) < 2:
        return await msg.reply("Usage: `/del movie_name`")
    query = " ".join(msg.command[1:])
    await client.db.execute("DELETE FROM movies WHERE title LIKE ?", (f"%{query}%",))
    await client.db.commit()
    await msg.reply(f"🗑️ `{query}` removed from Database.")

# ================= STORAGE INDEXING =================

@app.on_message(filters.chat(STORAGE_CHANNEL) & (filters.video | filters.document))
async def add_to_db(client, msg):
    file = msg.video or msg.document
    title = clean_name(msg.caption or file.file_name or "Unknown")
    await client.db.execute("INSERT INTO movies (title, file_id) VALUES (?, ?)", (title, file.file_id))
    await client.db.commit()
    # Confirmation message in storage channel
    await msg.reply_text(f"✅ **Movie Added:** `{title}`")

# ================= SEARCH LOGIC =================

@app.on_message(filters.chat(SEARCH_CHAT) & filters.text & ~filters.command(["start", "pratap", "shortlink", "del"]))
async def search_movie(client, msg):
    query = clean_name(msg.text)
    if len(query) < 3: return

    try: await msg.delete()
    except: pass

    u_name = msg.from_user.first_name if msg.from_user else "User"
    u_id = msg.from_user.id if msg.from_user else "N/A"

    sm = await client.send_message(msg.chat.id, f"🔍 **Searching:** `{msg.text}`...")

    cursor = await client.db.execute("SELECT id, title FROM movies WHERE title LIKE ? LIMIT 1", (f"%{query}%",))
    res = await cursor.fetchone()

    if not res:
        await sm.edit(f"❌ `{msg.text}` nahi mili! Spelling check karein.")
        asyncio.create_task(delete_after_delay([sm], 15))
        return 

    db_id, db_title = res
    poster, m_title, m_rating, m_year = await get_tmdb_info(query)

    bot_url = f"https://t.me/{client.bot_info.username}?start=file_{db_id}"
    final_link = await get_shortlink(bot_url)

    btn = InlineKeyboardMarkup([[InlineKeyboardButton("🚀 DOWNLOAD / WATCH NOW", url=final_link)],
                                [InlineKeyboardButton("✨ JOIN CHANNEL ✨", url=MAIN_CHANNEL_LINK)]])

    # Watermark bilkul screenshot jaisa footer format
    cap = (f"✅ **Movie Mil Gayi!**\n\n🎬 **Naam:** `{db_title}`\n"
           f"🌟 **Rating:** `{m_rating}` | 📅 **Year:** `{m_year}`\n\n"
           f"👤 **User:** {u_name}\n🆔 **ID:** `{u_id}`\n\n"
           f"👁️ 2  [Movies 2026 - Cinema Pratap\"❤️🌹]({MAIN_CHANNEL_LINK})")

    if poster:
        res_msg = await client.send_photo(msg.chat.id, poster, caption=cap, reply_markup=btn)
    else:
        res_msg = await client.send_message(msg.chat.id, cap, reply_markup=btn)
    
    await sm.delete()
    asyncio.create_task(delete_after_delay([res_msg], 120))

# ================= START / FSUB =================

@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, msg):
    user_id = msg.from_user.id
    
    # PEHLE JOIN CHECK
    try:
        await client.get_chat_member(FSUB_CHANNEL, user_id)
    except UserNotParticipant:
        invite = (await client.get_chat(FSUB_CHANNEL)).invite_link
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("📢 JOIN CHANNEL FIRST 📢", url=invite)],
                                    [InlineKeyboardButton("✅ TRY AGAIN", url=f"https://t.me/{client.bot_info.username}?start={msg.command[1] if len(msg.command)>1 else ''}")]])
        return await msg.reply("❌ **Access Denied!**\n\nFile paane ke liye pehle niche diye gaye channel ko join karein.", reply_markup=btn)
    except Exception as e:
        print(f"Join Check Error: {e}")

    # AGAR JOIN HAI
    if len(msg.command) < 2:
        return await msg.reply("👋 Namaste! Group me movie search karein.")

    data = msg.command[1]
    if data.startswith("file_"):
        m_id = data.split("_")[1]
        cursor = await client.db.execute("SELECT file_id, title FROM movies WHERE id = ?", (m_id,))
        res = await cursor.fetchone()
        
        if res:
            f_id, title = res
            caption = (f"📂 **File Name:** `{title}`\n👤 **Admin:** pratap 🇮🇳❤️\n\n"
                       f"🚀 **Channel:** {MAIN_CHANNEL_LINK}\n\n"
                       f"👁️ 2  [Movies 2026 - Cinema Pratap\"❤️🌹]({MAIN_CHANNEL_LINK})\n\n"
                       f"⚠️ **Warning:** 2 minute me delete ho jayegi!")
            
            sf = await client.send_cached_media(msg.chat.id, f_id, caption=caption)
            asyncio.create_task(delete_after_delay([sf], 120))

if __name__ == "__main__":
    app.run()
async def shortlink(url):
    if not SHORTLINK_ENABLED or not SHORT_DOMAIN:
        return url
    try:
        api = f"https://{SHORT_DOMAIN}/api?api={SHORT_API_KEY}&url={url}"
        async with aiohttp.ClientSession() as s:
            async with s.get(api, timeout=10) as r:
                j = await r.json()
                return j.get("shortenedUrl", url)
    except:
        return url

async def tmdb_data(query):
    if not TMDB_API_KEY:
        return None
    url = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={query}"
    async with aiohttp.ClientSession() as s:
        async with s.get(url) as r:
            d = await r.json()
            if d.get("results"):
                p = d["results"][0].get("poster_path")
                if p:
                    return f"https://image.tmdb.org/t/p/w300{p}"
    return None

# ================== ADD MOVIE ==================
@bot.on_message(filters.chat(STORAGE_CHANNEL) & (filters.video | filters.document))
async def add_movie(_, m):
    f = m.video or m.document
    title = clean_name(m.caption or f.file_name or "movie")
    await movies.insert_one({"title": title, "file_id": f.file_id})
    await m.reply(f"✅ Added: `{title}`")

# ================== SEARCH (GROUP ONLY) ==================
@bot.on_message(filters.chat(SEARCH_CHAT) & filters.text & ~filters.regex("^/"))
async def search(_, m):
    q = clean_name(m.text)
    if len(q) < 3:
        return

    r = await movies.find_one({"title": {"$regex": q, "$options": "i"}})
    if not r:
        return await m.reply("❌ Movie nahi mili")

    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=file_{r['_id']}"
    link = await shortlink(link)

    poster = await tmdb_data(q)

    cap = (
        f"🎬 **{r['title']}**\n\n"
        f"👤 User: {m.from_user.first_name}\n"
        f"🆔 ID: `{m.from_user.id}`"
    )

    btn = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🎬 GET MOVIE", url=link)]]
    )

    if poster:
        await m.reply_photo(poster, caption=cap, reply_markup=btn)
    else:
        await m.reply(cap, reply_markup=btn)

# ================== WRONG PLACE SEARCH ==================
@bot.on_message(filters.private & filters.text & ~filters.command([]))
async def wrong(_, m):
    await m.reply(
        "❌ Yahan search nahi hota bhai 🙏\n"
        "👇 Niche diye gaye group me search karo",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔍 SEARCH GROUP", url=SEARCH_GROUP_LINK)]]
        )
    )

# ================== START ==================
@bot.on_message(filters.private & filters.command("start"))
async def start(_, m):
    try:
        await bot.get_chat_member(FSUB_CHANNEL, m.from_user.id)
    except UserNotParticipant:
        return await m.reply(
            "❌ Pehle channel join karo",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("JOIN", url=MAIN_CHANNEL_LINK)]]
            )
        )

    if len(m.command) > 1 and m.command[1].startswith("file_"):
        mid = m.command[1].replace("file_","")
        r = await movies.find_one({"_id": ObjectId(mid)})
        if not r:
            return await m.reply("❌ File nahi mili")

        sent = await bot.send_cached_media(
            m.chat.id,
            r["file_id"],
            caption=WATERMARK
        )
        asyncio.create_task(auto_delete(sent))
    else:
        await m.reply("✅ Bot ready, group me movie search karo")

# ================= ADMIN COMMANDS =================

# 1. Check Movie Count
@bot.on_message(filters.command("pratap") & filters.user(ADMIN_IDS))
async def stats(_, msg):
    c = await movies.count_documents({})
    await msg.reply(f"📊 **Total Movies:** {c}")

# 2. Shortener On/Off
@bot.on_message(filters.command("shortn") & filters.user(ADMIN_IDS))
async def short_toggle(_, msg):
    global SHORTLINK_ENABLED
    if len(msg.command) < 2:
        return await msg.reply("Use: `/shortn on` ya `/shortn off`")
    
    state = msg.command[1].lower()
    if state == "on":
        SHORTLINK_ENABLED = True
        await msg.reply("✅ Shortener **ON** kar diya.")
    elif state == "off":
        SHORTLINK_ENABLED = False
        await msg.reply("❌ Shortener **OFF** kar diya.")
    else:
        await msg.reply("❌ Sahi command likho: on ya off")

# 3. Delete Movie
@bot.on_message(filters.command("del") & filters.user(ADMIN_IDS))
async def delete(_, msg):
    if len(msg.command) < 2:
        return await msg.reply("❌ Movie ka naam bhi likho delete karne ke liye.\nExample: `/del Pathaan`")
        
    q = " ".join(msg.command[1:])
    r = await movies.delete_many({"title": {"$regex": q, "$options": "i"}})
    await msg.reply(f"🗑 **Deleted:** {r.deleted_count} files jinke naam me '{q}' tha.")

# ================= PRIVATE TEXT (NO COMMAND) =================
@bot.on_message(filters.private & filters.text & ~filters.command([]))
async def private_text(_, m):
    await m.reply(
        "❌ Yahan movie search nahi hota bhai 🙏\n\n"
        "👉 Movie search karne ke liye GROUP me jao 👇",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔍 SEARCH GROUP", url=SEARCH_GROUP_LINK)]]
        )
        )
    
# ================== RUN ==================
async def main():
    await start_web()
    await bot.start()
    await idle()

if __name__ == "__main__":
    asyncio.run(main())
