import logging

import discord
from discord import app_commands

log = logging.getLogger("bot")


class UserError(Exception):
    """給使用者看的錯誤訊息（會以 ❌ 開頭私訊回覆）。"""


async def send_error(interaction: discord.Interaction, error: Exception) -> None:
    original = getattr(error, "original", error)
    if isinstance(original, UserError):
        text = f"❌ {original}"
    elif isinstance(error, app_commands.MissingPermissions):
        text = "❌ 你沒有權限使用這個指令。"
    elif isinstance(original, discord.Forbidden):
        text = "❌ 機器人權限不足，請確認機器人的身分組權限（建議給「管理員」，並把機器人的身分組拉到最上面）。"
    else:
        log.error("Unhandled error", exc_info=error)
        text = "❌ 發生未預期的錯誤，請稍後再試。"

    try:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
    except discord.HTTPException:
        pass
