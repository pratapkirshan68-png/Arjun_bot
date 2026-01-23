import os
os.system("pip install motor dnspython pyrogram tgcrypto")
import re
import asyncio
import aiohttp
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import UserNotParticipant
from bson.objectid import ObjectId

# ================= CONFIGURATION =================
API_ID = 24926770             
API_HASH = "f8de17dede0af9915d3bf8e05a5c66c2"    
BOT_TOKEN = "8418937721:AAEalOP7pfsjf3MCE9wgLJx2Rms1csb28S8"  
TMDB_API_KEY = "352bb22d327ce87e69217a7ae4cbe598" 

# Yahan apna MongoDB ka link dalein
MONGO_URL = "mongodb+srv://janngaming87:arjun@cluster0.fikom4m.mongodb.net/?appName=Cluster0"

SHORT_DOMAIN = "arolinks.com"
SHORT_API_KEY = "badc700f4f81f6524c5bc08bc4c7c6cd286a9298"

STORAGE_CHANNEL = -1003536285620  
SEARCH_CHAT = -1003556253573      
FSUB_CHANNEL = -1003652459294   
MAIN_CHANNEL_LINK = "https://t.me/Movies2026Cinema" 

ADMIN_IDS = [6429831771]

# Global variables
SHORTLINK_ENABLED = True 

class MovieBot(Client):
    def __init__(self):
        super().__init__("pratap_session", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
        self.db_client = AsyncIOMotorClient(MONGO_URL)
        self.db = self.db_client["PratapCinemaBot"]
        self.movies = self.db["movies"]

    async def start(self):
        await super().start()
        self.bot_info = await self.get_me()
        print(f"🚀 MONGO BOT @{self.bot_info.username} STARTED")

    async def stop(self, *args):
        await super().stop()

app = MovieBot()

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
    except: pass
    return url

def clean_name(text):
    if not text: return ""
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
