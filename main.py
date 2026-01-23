import os, re, asyncio, aiohttp
from aiohttp import web
from pyrogram import Client, filters
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import UserNotParticipant
from bson.objectid import ObjectId

# ================== ENV ==================
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URL = os.environ.get("MONGO_URL")

STORAGE_CHANNEL = int(os.environ.get("STORAGE_CHANNEL"))
SEARCH_CHAT = int(os.environ.get("SEARCH_CHAT"))
FSUB_CHANNEL = int(os.environ.get("FSUB_CHANNEL"))
MAIN_CHANNEL_LINK = os.environ.get("MAIN_CHANNEL_LINK")

ADMIN_IDS = [int(x) for x in re.findall(r"\d+", os.environ.get("ADMIN_IDS", ""))]

SHORT_DOMAIN = os.environ.get("SHORT_DOMAIN", "")
SHORT_API_KEY = os.environ.get("SHORT_API_KEY", "")
SHORTLINK_ENABLED = True

# ================== DB ==================
mongo = AsyncIOMotorClient(MONGO_URL)
db = mongo["PratapCinemaBot"]["movies"]

# ================== BOT ==================
app = Client(
    "pratap_session",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# ================== WEB SERVER (Render) ==================
async def health(request):
    return web.Response(text="Bot is alive")

async def start_web_server():
    app_web = web.Application()
    app_web.router.add_get("/", health)
    runner = web.AppRunner(app_web)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    await web.TCPSite(runner, "0.0.0.0", port).start()

# ================== HELPERS ==================
def clean_name(text):
    if not text:
        return ""
    text = text.lower()
    junk = ["1080p","720p","480p","x264","x265","hevc","bluray","webrip","hindi","english"]
    for j in junk:
        text = text.replace(j, "")
    return " ".join(text.replace(".", " ").replace("_", " ").split())

async def get_short(url):
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

async def auto_delete(msg, sec=120):
    await asyncio.sleep(sec)
    try:
        await msg.delete()
    except:
        pass

# ================== ADMIN COMMANDS ==================
@app.on_message(filters.command("pratap.movie") & filters.user(ADMIN_IDS))
async def total_movies(_, m):
    count = await db.count_documents({})
    await m.reply(f"📊 Total Movies: `{count}`")

@app.on_message(filters.command("shortnr") & filters.user(ADMIN_IDS))
async def shortnr(_, m):
    global SHORTLINK_ENABLED
    if len(m.command) < 2:
        return
    SHORTLINK_ENABLED = m.command[1].lower() == "on"
    await m.reply(f"🔗 Shortlink {'ON' if SHORTLINK_ENABLED else 'OFF'}")

@app.on_message(filters.command("del") & filters.user(ADMIN_IDS))
async def delete_movie(_, m):
    if len(m.command) < 2:
        return
    q = " ".join(m.command[1:])
    r = await db.delete_many({"title": {"$regex": q, "$options": "i"}})
    await m.reply(f"🗑️ Deleted `{r.deleted_count}` movies")

# ================== STORAGE ==================
@app.on_message(filters.chat(STORAGE_CHANNEL) & (filters.video | filters.document))
async def add_movie(_, m):
    f = m.video or m.document
    title = clean_name(m.caption or f.file_name)
    await db.update_one(
        {"title": title},
        {"$set": {"file_id": f.file_id}},
        upsert=True
    )
    await m.reply_text(f"✅ Movie Added: `{title}`")

# ================== SEARCH ==================
@app.on_message(filters.chat(SEARCH_CHAT) & filters.text)
async def search_movie(_, m):
    if "http://" in m.text or "https://" in m.text:
        return await m.reply("❌ Link allowed nahi. Sirf movie name search karein.")

    q = clean_name(m.text)
    if len(q) < 3:
        return

    wait = await m.reply("🔍 Searching...")
    res = await db.find_one({"title": {"$regex": q, "$options": "i"}})

    if not res:
        await wait.edit("😢 Movie nahi mili")
        await auto_delete(wait, 15)
        return

    me = await app.get_me()
    start_link = f"https://t.me/{me.username}?start=file_{res['_id']}"
    final_link = await get_short(start_link)

    btn = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🎬 GET LINK", url=final_link)]]
    )

    cap = (
        f"🎥 **Movie:** `{res['title']}`\n"
        f"👤 **User:** {m.from_user.first_name}\n"
        f"🆔 `{m.from_user.id}`"
    )

    msg = await m.reply(cap, reply_markup=btn)
    await wait.delete()
    await auto_delete(msg)

# ================== START / FSUB ==================
@app.on_message(filters.command("start") & filters.private)
async def start(_, m):
    try:
        await app.get_chat_member(FSUB_CHANNEL, m.from_user.id)
    except UserNotParticipant:
        btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 JOIN CHANNEL", url=MAIN_CHANNEL_LINK)],
            [InlineKeyboardButton(
                "✅ TRY AGAIN",
                url=f"https://t.me/{(await app.get_me()).username}?start={m.command[1] if len(m.command)>1 else ''}"
            )]
        ])
        return await m.reply("❌ Pehle channel join karo", reply_markup=btn)

    if len(m.command) > 1 and m.command[1].startswith("file_"):
        mid = m.command[1].split("_")[1]
        r = await db.find_one({"_id": ObjectId(mid)})
        if r:
            msg = await m.reply_cached_media(r["file_id"])
            await auto_delete(msg)

# ================== MAIN ==================
async def main():
    await start_web_server()
    await app.start()
    await asyncio.Event().wait()

asyncio.run(main())                        p_path = res.get('backdrop_path') or res.get('poster_path')
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

@app.on_message(filters.command("shontrn") & filters.user(ADMIN_IDS))
async def toggle_shortlink_cmd(client, msg):
    global SHORTLINK_ENABLED
    if len(msg.command) < 2:
        return await msg.reply("Usage: `/shontrn on` or `/shontrn off`")
    
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
    result = await client.movies.delete_many({"title": {"$regex": query, "$options": "i"}})
    await msg.reply(f"🗑️ Deleted `{result.deleted_count}` movies matching `{query}`.")

# ================= STORAGE INDEXING =================

@app.on_message(filters.chat(STORAGE_CHANNEL) & (filters.video | filters.document))
async def add_to_db(client, msg):
    file = msg.video or msg.document
    title = clean_name(msg.caption or file.file_name or "Unknown")
    await client.movies.insert_one({"title": title, "file_id": file.file_id})
    await msg.reply_text(f"✅ **Movie Added to Mongo:** `{title}`")

# ================= SEARCH LOGIC =================

@app.on_message(filters.chat(SEARCH_CHAT) & filters.text & ~filters.command(["start", "pratap", "shontrn", "del"]))
async def search_movie(client, msg):
    query = clean_name(msg.text)
    if len(query) < 3: return

    try: await msg.delete()
    except: pass

    u_name = msg.from_user.first_name if msg.from_user else "User"
    u_id = msg.from_user.id if msg.from_user else "N/A"

    sm = await client.send_message(msg.chat.id, f"🔍 **Searching:** `{msg.text}`...")

    res = await client.movies.find_one({"title": {"$regex": query, "$options": "i"}})

    if not res:
        await sm.edit(f"❌ `{msg.text}` nahi mili! Spelling check karein.")
        asyncio.create_task(delete_after_delay([sm], 15))
        return 

    db_id = str(res["_id"])
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
    
    try:
        await client.get_chat_member(FSUB_CHANNEL, user_id)
    except UserNotParticipant:
        invite_chat = await client.get_chat(FSUB_CHANNEL)
        invite = invite_chat.invite_link or MAIN_CHANNEL_LINK
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("📢 JOIN CHANNEL FIRST 📢", url=invite)],
                                    [InlineKeyboardButton("✅ TRY AGAIN", url=f"https://t.me/{client.bot_info.username}?start={msg.command[1] if len(msg.command)>1 else ''}")]])
        return await msg.reply("❌ **Access Denied!**\n\nFile paane ke liye pehle niche diye gaye channel ko join karein.", reply_markup=btn)
    except Exception: pass

    if len(msg.command) < 2:
        return await msg.reply("👋 Namaste! Group me movie search karein.")

    data = msg.command[1]
    if data.startswith("file_"):
        m_id = data.split("_")[1]
        res = await client.movies.find_one({"_id": ObjectId(m_id)})
        
        if res:
            f_id, title = res["file_id"], res["title"]
            caption = (f"📂 **File Name:** `{title}`\n👤 **Admin:** pratap 🇮🇳❤️\n\n"
                       f"🚀 **Channel:** {MAIN_CHANNEL_LINK}\n\n"
                       f"👁️ 2  [Movies 2026 - Cinema Pratap\"❤️🌹]({MAIN_CHANNEL_LINK})\n\n"
                       f"⚠️ **Warning:** 2 minute me delete ho jayegi!")
            
            sf = await client.send_cached_media(msg.chat.id, f_id, caption=caption)
            asyncio.create_task(delete_after_delay([sf], 120))

if __name__ == "__main__":
    app.run()                    if data.get('results'):
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

@app.on_message(filters.command("shontrn") & filters.user(ADMIN_IDS))
async def toggle_shortlink_cmd(client, msg):
    global SHORTLINK_ENABLED
    if len(msg.command) < 2:
        return await msg.reply("Usage: `/shontrn on` or `/shontrn off`")
    
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
    result = await client.movies.delete_many({"title": {"$regex": query, "$options": "i"}})
    await msg.reply(f"🗑️ Deleted `{result.deleted_count}` movies matching `{query}`.")

# ================= STORAGE INDEXING =================

@app.on_message(filters.chat(STORAGE_CHANNEL) & (filters.video | filters.document))
async def add_to_db(client, msg):
    file = msg.video or msg.document
    title = clean_name(msg.caption or file.file_name or "Unknown")
    await client.movies.insert_one({"title": title, "file_id": file.file_id})
    await msg.reply_text(f"✅ **Movie Added to Mongo:** `{title}`")

# ================= SEARCH LOGIC =================

@app.on_message(filters.chat(SEARCH_CHAT) & filters.text & ~filters.command(["start", "pratap", "shontrn", "del"]))
async def search_movie(client, msg):
    query = clean_name(msg.text)
    if len(query) < 3: return

    try: await msg.delete()
    except: pass

    u_name = msg.from_user.first_name if msg.from_user else "User"
    u_id = msg.from_user.id if msg.from_user else "N/A"

    sm = await client.send_message(msg.chat.id, f"🔍 **Searching:** `{msg.text}`...")

    res = await client.movies.find_one({"title": {"$regex": query, "$options": "i"}})

    if not res:
        await sm.edit(f"❌ `{msg.text}` nahi mili! Spelling check karein.")
        asyncio.create_task(delete_after_delay([sm], 15))
        return 

    db_id = str(res["_id"])
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
    
    try:
        await client.get_chat_member(FSUB_CHANNEL, user_id)
    except UserNotParticipant:
        invite_chat = await client.get_chat(FSUB_CHANNEL)
        invite = invite_chat.invite_link or MAIN_CHANNEL_LINK
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("📢 JOIN CHANNEL FIRST 📢", url=invite)],
                                    [InlineKeyboardButton("✅ TRY AGAIN", url=f"https://t.me/{client.bot_info.username}?start={msg.command[1] if len(msg.command)>1 else ''}")]])
        return await msg.reply("❌ **Access Denied!**\n\nFile paane ke liye pehle niche diye gaye channel ko join karein.", reply_markup=btn)
    except Exception: pass

    if len(msg.command) < 2:
        return await msg.reply("👋 Namaste! Group me movie search karein.")

    data = msg.command[1]
    if data.startswith("file_"):
        m_id = data.split("_")[1]
        res = await client.movies.find_one({"_id": ObjectId(m_id)})
        
        if res:
            f_id, title = res["file_id"], res["title"]
            caption = (f"📂 **File Name:** `{title}`\n👤 **Admin:** pratap 🇮🇳❤️\n\n"
                       f"🚀 **Channel:** {MAIN_CHANNEL_LINK}\n\n"
                       f"👁️ 2  [Movies 2026 - Cinema Pratap\"❤️🌹]({MAIN_CHANNEL_LINK})\n\n"
                       f"⚠️ **Warning:** 2 minute me delete ho jayegi!")
            
            sf = await client.send_cached_media(msg.chat.id, f_id, caption=caption)
            asyncio.create_task(delete_after_delay([sf], 120))

if __name__ == "__main__":
    app.run()
