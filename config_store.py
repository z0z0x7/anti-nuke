import asyncio
import io
import json
import logging

import discord

log = logging.getLogger("bot")

CHANNEL_NAME = "防炸群-設定"
TOPIC_MARKER = "antiraid-config"
FILENAME = "config.json"


class ConfigStore:
    """把每個伺服器的設定存成 JSON 附件，放在一個只有管理員看得到的頻道。
    Railway 重新部署會清空硬碟，存在 Discord 裡就不會遺失，也不用另外付費開 Volume。"""

    def __init__(self, bot: discord.Client):
        self.bot = bot
        self.cache: dict[int, dict] = {}
        self.locks: dict[int, asyncio.Lock] = {}

    def _lock(self, guild_id: int) -> asyncio.Lock:
        return self.locks.setdefault(guild_id, asyncio.Lock())

    def _find_channel(self, guild: discord.Guild):
        for ch in guild.text_channels:
            if ch.topic and ch.topic.startswith(TOPIC_MARKER):
                return ch
        return None

    async def _find_message(self, ch: discord.TextChannel):
        async for m in ch.history(limit=20):
            if m.author.id == self.bot.user.id and any(a.filename == FILENAME for a in m.attachments):
                return m
        return None

    async def get(self, guild: discord.Guild) -> dict:
        if guild.id in self.cache:
            return self.cache[guild.id]
        async with self._lock(guild.id):
            if guild.id not in self.cache:
                data = {}
                ch = self._find_channel(guild)
                msg = await self._find_message(ch) if ch else None
                if msg:
                    att = next(a for a in msg.attachments if a.filename == FILENAME)
                    try:
                        data = json.loads(await att.read())
                    except Exception:
                        log.exception("讀取 %s 的設定失敗", guild.name)
                self.cache[guild.id] = data
        return self.cache[guild.id]

    async def save(self, guild: discord.Guild, data: dict) -> None:
        async with self._lock(guild.id):
            self.cache[guild.id] = data
            ch = self._find_channel(guild)
            if ch is None:
                ch = await guild.create_text_channel(
                    CHANNEL_NAME, topic=f"{TOPIC_MARKER}：機器人的設定存在這裡，請不要刪除這個頻道",
                    overwrites={
                        guild.default_role: discord.PermissionOverwrite(view_channel=False),
                        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                                              attach_files=True, read_message_history=True),
                    }, reason="儲存機器人設定")
            file = discord.File(io.BytesIO(json.dumps(data, ensure_ascii=False, indent=2).encode()),
                                filename=FILENAME)
            msg = await self._find_message(ch)
            if msg:
                await msg.edit(attachments=[file])
            else:
                await ch.send("⚙️ 機器人設定檔（請不要刪除）", file=file)

    async def update(self, guild: discord.Guild, key: str, value) -> None:
        data = dict(await self.get(guild))
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
        await self.save(guild, data)
