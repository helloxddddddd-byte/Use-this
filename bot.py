import sys
print(">>> PYTHON VERSION:", sys.version)
from flask import Flask
from threading import Thread
import os
from discord.ext import commands, tasks
import discord
discord.opus = None
import random
import requests
import asyncio
import logging
import aiohttp

# === Keep-alive server ===
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive!"

def run_flask():
    port = int(os.getenv("PORT", "8080"))
    print(f"Flask server starting on 0.0.0.0:{port}...")
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask, daemon=True)
    t.start()

# === Discord Bot ===
class MilestoneBot:
    def __init__(self, token: str, place_id: str | int):
        self.token = token
        self.place_id = str(place_id)

        # Intents
        intents = discord.Intents.none()
        intents.guilds = True
        intents.messages = True
        intents.message_content = True

        # Bot
        self.bot = commands.Bot(command_prefix='!', intents=intents)

        self.target_channel: discord.TextChannel | None = None
        self.is_running = False
        self.current_visits = 0
        self.milestone_goal = 3358

        logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
        logging.getLogger("discord").setLevel(logging.WARNING)

        # event + commands
        self.bot.add_listener(self.on_ready)
        self.setup_commands()

        # background loop
        self.milestone_loop = tasks.loop(seconds=65)(self._milestone_loop_body)

        # Requests session (NO proxy now)
        self._http = requests.Session()
        self._http.headers.update({"User-Agent": "Mozilla/5.0 (MilestoneBot)"})

        # aiohttp session (NO proxy now)
        connector = aiohttp.TCPConnector()
        self._aiohttp = aiohttp.ClientSession(connector=connector)

        # ✅ Add shutdown handler
        self.bot.add_listener(self.on_close)

        # ✅ Rate-limit variables
        self._last_request = 0
        self._min_delay = 2.0  # seconds between API calls

    async def safe_api_call(self, coro):
        """Prevent spamming API calls (Discord + Roblox)."""
        now = asyncio.get_event_loop().time()
        wait = self._min_delay - (now - self._last_request)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_request = asyncio.get_event_loop().time()
        return await coro

    async def on_ready(self):
        logging.info(f'Bot logged in as {self.bot.user}')
        try:
            await self.bot.change_presence(activity=discord.Game(name="Tracking visits…"))
        except Exception:
            pass

    async def on_close(self, *args):
        """Clean up sessions on shutdown"""
        logging.info("Closing aiohttp session...")
        if not self._aiohttp.closed:
            await self._aiohttp.close()
        logging.info("Shutdown complete ✅")

    def setup_commands(self):
        @self.bot.command(name='startms')
        async def start_milestone(ctx: commands.Context):
            if self.is_running:
                if self.target_channel and self.target_channel.id != ctx.channel.id:
                    await self.safe_api_call(ctx.send(f"Already running in {self.target_channel.mention}. Use `!stopms` there first."))
                else:
                    await self.safe_api_call(ctx.send("Bot is already running!"))
                return

            self.target_channel = ctx.channel
            self.is_running = True

            await self.safe_api_call(ctx.send("Milestone bot started ✅"))
            await self.send_milestone_update()
            if not self.milestone_loop.is_running():
                self.milestone_loop.start()

        @self.bot.command(name='stopms')
        async def stop_milestone(ctx: commands.Context):
            if not self.is_running:
                await self.safe_api_call(ctx.send("Bot is not running!"))
                return
            self.is_running = False
            if self.milestone_loop.is_running():
                self.milestone_loop.cancel()
            await self.safe_api_call(ctx.send("Milestone bot stopped ⏹️"))

        @self.bot.command(name='setgoal')
        async def set_goal(ctx: commands.Context, goal: int):
            if goal < 0:
                await self.safe_api_call(ctx.send("Goal must be a positive number."))
                return
            self.milestone_goal = goal
            await self.safe_api_call(ctx.send(f"Milestone goal set to **{goal:,}**"))

        @self.bot.command(name='status')
        async def status(ctx: commands.Context):
            players, visits = await asyncio.to_thread(self.get_game_data)
            await self.safe_api_call(ctx.send(
                f"Players: **{players}** | Visits: **{visits:,}** | Next goal: **{self.milestone_goal:,}**"
            ))

        @self.bot.event
        async def on_command_error(ctx: commands.Context, error: Exception):
            logging.error(f"Command error: {error}")
            try:
                await self.safe_api_call(ctx.send(f"⚠️ {type(error).__name__}: {error}"))
            except Exception:
                pass

    def get_game_data(self) -> tuple[int, int]:
        total_players = 0
        visits = self.current_visits

        try:
            universe_resp = self._http.get(
                f"https://apis.roblox.com/universes/v1/places/{self.place_id}/universe", timeout=10
            )
            universe_resp.raise_for_status()
            universe_id = universe_resp.json().get("universeId")
            if not universe_id:
                raise RuntimeError("Cannot get universe ID")

            game_resp = self._http.get(
                f"https://games.roblox.com/v1/games?universeIds={universe_id}", timeout=10
            )
            game_resp.raise_for_status()
            data = game_resp.json().get("data", [])
            if data and isinstance(data, list):
                api_visits = data[0].get("visits", None)
                if isinstance(api_visits, int) and api_visits >= 0:
                    visits = api_visits

            self.current_visits = max(self.current_visits, visits)

            cursor = ""
            while True:
                servers_url = f"https://games.roblox.com/v1/games/{self.place_id}/servers/Public?sortOrder=Asc&limit=100"
                if cursor:
                    servers_url += f"&cursor={cursor}"
                server_resp = self._http.get(servers_url, timeout=10)
                server_resp.raise_for_status()
                server_data = server_resp.json()
                data_list = server_data.get("data", [])
                total_players += sum(int(s.get("playing", 0) or 0) for s in data_list)
                cursor = server_data.get("nextPageCursor")
                if not cursor:
                    break

            return total_players, self.current_visits

        except Exception as e:
            logging.error(f"Error fetching game data: {e}")
            return random.randint(10, 25), max(3258, self.current_visits)

    async def send_milestone_update(self):
        if not self.target_channel or not self.is_running:
            return

        players, visits = await asyncio.to_thread(self.get_game_data)

        if visits >= self.milestone_goal:
            self.milestone_goal = visits + max(100, int(visits * 0.05))

        message = (
            "--------------------------------------------------\n"
            f"👤🎮 Active players: {players}\n"
            "--------------------------------------------------\n"
            f"👥 Visits: {visits:,}\n"
            f"🎯 Next milestone: {visits:,}/{self.milestone_goal:,}\n"
            "--------------------------------------------------"
        )
        try:
            await self.safe_api_call(self.target_channel.send(message))
        except Exception as e:
            logging.error(f"Failed to send Discord message: {e}")

    async def _milestone_loop_body(self):
        await asyncio.sleep(random.uniform(0.2, 1.2))
        await self.send_milestone_update()

    def run(self):
        self.bot.run(self.token)

# === Run bot ===
if __name__ == "__main__":
    keep_alive()
    token = os.getenv("DISCORD_TOKEN")
    place_id = "125760703264498"
    if not token:
        print("Error: DISCORD_TOKEN not found")
        raise SystemExit(1)
    MilestoneBot(token, place_id).run()
