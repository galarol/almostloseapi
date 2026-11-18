import discord
from discord.ext import commands
import os
import random
import string
import time
from aiohttp import web
import asyncio
import sqlite3
from datetime import datetime, timedelta
from dotenv import load_dotenv
from pyngrok import ngrok, conf

load_dotenv()

# --- КОНФИГУРАЦИЯ ---
TOKEN = os.getenv("BOT_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", 0))
ROLE_ID = int(os.getenv("ROLE_ID", 0))
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "ludoman5rp")

# Ngrok токен (получите бесплатно на ngrok.com)
NGROK_TOKEN = "3BWh57tFwkOvnDiAFlVzxnAyWIi_5XSqHTDqzmsqt3unpymjm"

# ==================== БАЗА ДАННЫХ ====================
def init_db():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (discord_id INTEGER PRIMARY KEY, username TEXT, sub_end TIMESTAMP)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT, details TEXT, timestamp TIMESTAMP)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS forum_threads (id INTEGER PRIMARY KEY AUTOINCREMENT, author TEXT, title TEXT, content TEXT, timestamp TIMESTAMP)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS market_items (id INTEGER PRIMARY KEY AUTOINCREMENT, seller TEXT, title TEXT, price INTEGER, description TEXT, contact TEXT, timestamp TIMESTAMP)''')
    conn.commit()
    conn.close()

def add_log(action, details):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("INSERT INTO logs (action, details, timestamp) VALUES (?, ?, ?)", (action, details, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_logs():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT action, details, timestamp FROM logs ORDER BY id DESC LIMIT 10")
    rows = cursor.fetchall()
    conn.close()
    return [{"action": r[0], "details": r[1], "time": datetime.fromisoformat(r[2]).strftime("%H:%M")} for r in rows]

def get_stats():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    now = datetime.now().isoformat()
    cursor.execute("SELECT COUNT(*) FROM users WHERE sub_end > ?", (now,))
    active_subs = cursor.fetchone()[0]
    conn.close()
    return {"total_users": total_users, "active_subs": active_subs}

def get_sub_status(discord_id):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT sub_end FROM users WHERE discord_id = ?", (discord_id,))
    row = cursor.fetchone()
    conn.close()
    if not row or not row[0]: return 0, "Нет подписки"
    end_date = datetime.fromisoformat(row[0])
    now = datetime.now()
    if end_date < now: return 0, "Истекла"
    return (end_date - now).days, end_date.strftime("%d.%m.%Y %H:%M")

def add_sub(discord_id, username, days):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT sub_end FROM users WHERE discord_id = ?", (discord_id,))
    row = cursor.fetchone()
    now = datetime.now()
    if row and row[0]:
        current_end = datetime.fromisoformat(row[0])
        new_end = (current_end if current_end > now else now) + timedelta(days=days)
    else:
        new_end = now + timedelta(days=days)
    cursor.execute('''INSERT INTO users (discord_id, username, sub_end) VALUES (?, ?, ?) 
                      ON CONFLICT(discord_id) DO UPDATE SET sub_end=excluded.sub_end, username=excluded.username''', 
                   (discord_id, username, new_end.isoformat()))
    conn.commit()
    conn.close()
    return new_end.strftime("%d.%m.%Y %H:%M")

def remove_sub(discord_id):
    conn = sqlite3.connect('database.db')
    conn.execute("UPDATE users SET sub_end = ? WHERE discord_id = ?", (datetime.now().isoformat(), discord_id))
    conn.commit()
    conn.close()

# ==================== БОТ ====================
active_codes = {}
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.command(name="sub")
@commands.has_permissions(administrator=True)
async def manage_sub(ctx, action: str = None, duration: str = None, member: discord.Member = None):
    if action is None or action.lower() != "give" or not duration or not member:
        return await ctx.send("❌ Использование: `!sub give <дни> <@юзер>`")
    days_str = duration.lower().replace('d', '').replace('д', '')
    try: days = int(days_str)
    except ValueError: return await ctx.send("❌ Неверный формат дней.")
    
    new_date = add_sub(member.id, member.name, days)
    add_log("Give Sub", f"{member.name} +{days} days")
    embed = discord.Embed(title="💎 Подписка обновлена", description=f"Пользователю {member.mention} выдана подписка.\n**Действует до:** {new_date}", color=0x4B7BF5)
    await ctx.send(embed=embed)

# ==================== API (WEB SERVER) ====================
@web.middleware
async def cors_middleware(request, handler):
    if request.method == "OPTIONS": 
        response = web.Response()
    else:
        try: 
            response = await handler(request)
        except web.HTTPException as ex: 
            response = ex
    
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, DELETE, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    return response

class VerifyView(discord.ui.View):
    def __init__(self, code):
        super().__init__(timeout=None)
        self.code = code

    @discord.ui.button(label="Подтвердить вход", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.code not in active_codes: 
            return await interaction.response.send_message("❌ Код устарел или уже использован.", ephemeral=True)
        
        active_codes[self.code]["status"] = "success"
        active_codes[self.code]["username"] = interaction.user.name
        
        conn = sqlite3.connect('database.db')
        conn.execute("INSERT OR IGNORE INTO users (discord_id, username) VALUES (?, ?)", (interaction.user.id, interaction.user.name))
        conn.commit(); conn.close()
        
        add_log("Login", interaction.user.name)
        
        guild = bot.get_guild(GUILD_ID)
        if guild:
            member = guild.get_member(interaction.user.id)
            if member:
                role = guild.get_role(ROLE_ID)
                if role:
                    try: await member.add_roles(role)
                    except: pass
        
        await interaction.response.send_message("✅ Успешно! Теперь вы можете обновить страницу сайта.", ephemeral=True)
        self.stop()

@bot.event
async def on_ready():
    init_db()
    print(f"Бот {bot.user} готов!")
    try: await bot.tree.sync()
    except: pass

# --- API Endpoints ---

async def generate_code(request):
    try:
        data = await request.json()
        username = data.get("username", "").strip()
        guild = bot.get_guild(GUILD_ID)
        if not guild: return web.json_response({"error": "Бот оффлайн"}, status=500)
        
        member = None
        for m in guild.members:
            if m.name.lower() == username.lower() or (m.global_name and m.global_name.lower() == username.lower()):
                member = m
                break
                
        if not member: return web.json_response({"error": "Пользователь не найден на сервере Discord."}, status=404)
        
        code = ''.join(random.choices(string.digits, k=6))
        active_codes[code] = {"status": "pending", "username": username, "timestamp": time.time()}
        
        embed = discord.Embed(
            title="🔐 Вход в AlmostLose", 
            description=f"Код подтверждения:\n**`{code}`**\n\nНажмите кнопку ниже.", 
            color=0x4B7BF5
        )
        embed.set_thumbnail(url="https://cdn-icons-png.flaticon.com/512/919/919825.png")
        
        try:
            await member.send(embed=embed, view=VerifyView(code))
            return web.json_response({"code": code})
        except discord.Forbidden: 
            return web.json_response({"error": "Ошибка: Закройте настройки конфиденциальности -> Разрешить ЛС от участников сервера."}, status=403)
    except Exception as e: 
        return web.json_response({"error": str(e)}, status=500)

async def check_status(request):
    code = request.query.get("code")
    if not code or code not in active_codes: 
        return web.json_response({"status": "not_found"})
    return web.json_response({"status": active_codes[code]["status"], "username": active_codes[code]["username"]})

async def get_user_info(request):
    username = request.query.get("username")
    if not username: return web.json_response({"error": "No username"}, status=400)
    
    guild = bot.get_guild(GUILD_ID)
    member = None
    for m in guild.members:
        if m.name.lower() == username.lower() or (m.global_name and m.global_name.lower() == username.lower()):
            member = m
            break
            
    if not member: return web.json_response({"error": "Not found"}, status=404)
    
    days_left, date_str = get_sub_status(member.id)
    is_admin = (member.name.lower() == ADMIN_USERNAME.lower())
    return web.json_response({"days_left": days_left, "date": date_str, "is_admin": is_admin, "discord_id": member.id})

async def admin_give_sub(request):
    try:
        data = await request.json()
        if data.get("admin_name").lower() != ADMIN_USERNAME.lower(): return web.json_response({"error": "Доступ запрещен"}, status=403)
        guild = bot.get_guild(GUILD_ID)
        member = next((m for m in guild.members if m.name.lower() == data.get("target_name").lower()), None)
        if not member: return web.json_response({"error": "Не найден"}, status=404)
        new_date = add_sub(member.id, member.name, int(data.get("days", 0)))
        return web.json_response({"success": True, "date": new_date})
    except Exception as e: return web.json_response({"error": str(e)}, status=500)

async def admin_remove_sub(request):
    try:
        data = await request.json()
        if data.get("admin_name").lower() != ADMIN_USERNAME.lower(): return web.json_response({"error": "Доступ запрещен"}, status=403)
        guild = bot.get_guild(GUILD_ID)
        member = next((m for m in guild.members if m.name.lower() == data.get("target_name").lower()), None)
        if not member: return web.json_response({"error": "Не найден"}, status=404)
        remove_sub(member.id)
        return web.json_response({"success": True})
    except Exception as e: return web.json_response({"error": str(e)}, status=500)

async def admin_get_logs(request):
    return web.json_response({"logs": get_logs(), "stats": get_stats()})

async def get_threads(request):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id, author, title, content, timestamp FROM forum_threads ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    return web.json_response([{"id": r[0], "author": r[1], "title": r[2], "content": r[3], "date": datetime.fromisoformat(r[4]).strftime("%d.%m %H:%M")} for r in rows])

async def create_thread(request):
    try:
        data = await request.json()
        conn = sqlite3.connect('database.db')
        conn.execute("INSERT INTO forum_threads (author, title, content, timestamp) VALUES (?, ?, ?, ?)", (data["author"], data["title"], data["content"], datetime.now().isoformat()))
        conn.commit(); conn.close()
        return web.json_response({"success": True})
    except Exception as e: return web.json_response({"error": str(e)}, status=500)

async def delete_thread(request):
    try:
        data = await request.json()
        if data.get("admin_name").lower() != ADMIN_USERNAME.lower(): return web.json_response({"error": "Только админ"}, status=403)
        conn = sqlite3.connect('database.db')
        conn.execute("DELETE FROM forum_threads WHERE id = ?", (data["id"],))
        conn.commit(); conn.close()
        return web.json_response({"success": True})
    except Exception as e: return web.json_response({"error": str(e)}, status=500)

async def get_market(request):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id, seller, title, price, description, contact FROM market_items ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    return web.json_response([{"id": r[0], "seller": r[1], "title": r[2], "price": r[3], "desc": r[4], "contact": r[5]} for r in rows])

async def create_market(request):
    try:
        data = await request.json()
        conn = sqlite3.connect('database.db')
        conn.execute("INSERT INTO market_items (seller, title, price, description, contact, timestamp) VALUES (?, ?, ?, ?, ?, ?)", 
                     (data["author"], data["title"], int(data["price"]), data.get("desc", ""), data.get("contact", ""), datetime.now().isoformat()))
        conn.commit(); conn.close()
        return web.json_response({"success": True})
    except Exception as e: return web.json_response({"error": str(e)}, status=500)

async def start_web_server():
    app = web.Application(middlewares=[cors_middleware])
    app.router.add_post('/generate', generate_code)
    app.router.add_get('/check', check_status)
    app.router.add_get('/user_info', get_user_info)
    app.router.add_post('/admin/give_sub', admin_give_sub)
    app.router.add_post('/admin/remove_sub', admin_remove_sub)
    app.router.add_get('/admin/logs', admin_get_logs)
    app.router.add_get('/forum/threads', get_threads)
    app.router.add_post('/forum/create', create_thread)
    app.router.add_post('/forum/delete', delete_thread)
    app.router.add_get('/market/items', get_market)
    app.router.add_post('/market/create', create_market)
    
    runner = web.AppRunner(app)
    await runner.setup()
    PORT = int(os.getenv("PORT", 8080)) # Render передает порт через переменную окружения
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    print(f"API Server running on port {PORT}")
    await site.start()
    print("API Server running on port 15676")
    
    # Ждём 2 секунды чтобы сервер точно запустился
    await asyncio.sleep(2)
    
    # Запуск ngrok туннеля
    try:
        conf.get_default().auth_token = NGROK_TOKEN
        public_url = ngrok.connect(15676, "http")
        print(f"\n{'='*60}")
        print(f"🌐 ВАШ ПУБЛИЧНЫЙ URL: {public_url}")
        print(f"{'='*60}\n")
    except Exception as e:
        print(f"❌ Ошибка ngrok: {e}")

async def main():
    asyncio.create_task(start_web_server())
    await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
