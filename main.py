import asyncio
import logging
import os

import discord
from discord.ext import commands

from config_store import ConfigStore
from utils import send_error

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID", "").strip()

COGS = ["cogs.antinuke"]


class Bot(commands.Bot):
    def __init__(self):
        # 預設 intents 已包含審核日誌（moderation）和訊息事件，目前不需要開特權 intents
        super().__init__(command_prefix=commands.when_mentioned, intents=discord.Intents.default())
        self.config = ConfigStore(self)

    async def setup_hook(self):
        self.tree.error(send_error)
        for cog in COGS:
            await self.load_extension(cog)

        if GUILD_ID:
            guild = discord.Object(int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            try:
                synced = await self.tree.sync(guild=guild)
                log.info("已同步 %d 個斜線指令到伺服器 %s", len(synced), GUILD_ID)
                return
            except discord.Forbidden:
                log.error("無法存取伺服器 %s：請確認 GUILD_ID 正確、機器人已加入該伺服器，"
                          "且邀請時有勾 applications.commands。改用全域同步（最多 1 小時才會出現）。",
                          GUILD_ID)
                self.tree.clear_commands(guild=guild)
        synced = await self.tree.sync()
        log.info("已全域同步 %d 個斜線指令", len(synced))

    async def on_ready(self):
        log.info("已登入：%s (%s)", self.user, self.user.id)


async def main():
    if not TOKEN:
        raise SystemExit("請在 Railway 的 Variables 設定 DISCORD_TOKEN")
    delay = 60
    while True:
        try:
            async with Bot() as bot:
                await bot.start(TOKEN)
            return
        except discord.HTTPException as error:
            if error.status != 429:
                raise
            # Railway 的 IP 是很多人共用的，偶爾會被 Discord（Cloudflare 1015）暫時封鎖。
            # 直接當掉的話 Railway 會馬上重開、一直重連，反而被擋更久，所以改成等一下再試。
            log.error("Discord 暫時擋住了這台主機的連線（429／Cloudflare 1015），%d 秒後自動重試…", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 900)


if __name__ == "__main__":
    asyncio.run(main())
