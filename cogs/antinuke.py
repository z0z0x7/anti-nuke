import asyncio
import copy
import datetime
import logging
import time
from collections import defaultdict, deque
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils import UserError

log = logging.getLogger("bot")

RULES = {
    "channel_delete": "🗑️ 刪除頻道",
    "msg_flood": "💬 大量傳訊息",
    "msg_channels": "📢 跨頻道洗版",
}
DEFAULTS = {
    "enabled": False,
    "log_channel": None,
    "quarantine_role": None,
    "quarantine_channel": None,
    "timeout_minutes": 60,
    "whitelist": [],
    # [次數, 秒數]：秒數內達到次數就處理
    "limits": {"channel_delete": [3, 60], "msg_flood": [10, 10], "msg_channels": [4, 10]},
    # 被隔離的人：{user_id: {"roles": [role_id, ...], "at": 時間, "reason": 原因}}
    "quarantined": {},
}
MAX_TIMEOUT = 40320  # Discord 禁言上限 28 天
COOLDOWN = 60  # 同一個人處理過後，這段時間內不重複處理
QUARANTINE_ROLE_NAME = "🔒 隔離中"
QUARANTINE_CHANNEL_NAME = "🔒隔離區"
NEEDED_PERMS = {
    "view_audit_log": "檢視審核日誌",
    "manage_roles": "管理身分組",
    "manage_channels": "管理頻道",
    "moderate_members": "禁言成員",
    "kick_members": "踢出成員",
    "manage_messages": "管理訊息",
    "manage_webhooks": "管理 Webhook",
}
RED, GREEN, YELLOW = 0xF04747, 0x43B581, 0xFAA61A


def load_cfg(raw: Optional[dict]) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    for key, value in copy.deepcopy(raw or {}).items():
        if key == "limits":
            cfg["limits"].update(value)
        else:
            cfg[key] = value
    return cfg


def describe_rule(key: str, limit: list) -> str:
    count, seconds = limit
    unit = {"channel_delete": f"刪除 {count} 個頻道", "msg_flood": f"傳 {count} 則訊息",
            "msg_channels": f"在 {count} 個不同頻道發言"}[key]
    return f"{seconds} 秒內{unit}"


def user_line(user) -> str:
    return f"{user.mention}（`{user}`，ID `{user.id}`）"


# ---------- 偵測邏輯（不碰 Discord，方便用假資料測試） ----------

class ActionTracker:
    """記錄每個人最近做了幾次某個動作，達到門檻就回傳 True。"""

    def __init__(self):
        self.events: dict[tuple, deque] = defaultdict(deque)

    def hit(self, key: tuple, count: int, seconds: int, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        q = self.events[key]
        q.append(now)
        while q and now - q[0] > seconds:
            q.popleft()
        if len(q) >= count:
            q.clear()
            return True
        return False


class MessageTracker:
    """記錄每個人最近的訊息，判斷是不是大量傳訊息或跨頻道洗版。"""

    def __init__(self):
        self.recent: dict[tuple, deque] = defaultdict(deque)

    def add(self, key: tuple, channel_id: int, message_id: int, flood: list, spread: list,
            now: Optional[float] = None) -> Optional[str]:
        now = time.monotonic() if now is None else now
        window = max(flood[1], spread[1])
        if len(self.recent) > 5000:  # 清掉很久沒講話的人，避免記憶體越用越多
            for k in [k for k, q in self.recent.items() if not q or now - q[-1][0] > window]:
                del self.recent[k]
        q = self.recent[key]
        q.append((now, channel_id, message_id))
        while q and now - q[0][0] > window:
            q.popleft()
        if sum(1 for t, _, _ in q if now - t <= flood[1]) >= flood[0]:
            return "msg_flood"
        if len({c for t, c, _ in q if now - t <= spread[1]}) >= spread[0]:
            return "msg_channels"
        return None

    def pop(self, key: tuple) -> list[tuple[int, int]]:
        """取出這個人最近的訊息（頻道 ID, 訊息 ID），用來刪除洗版訊息。"""
        return [(c, m) for _, c, m in self.recent.pop(key, ())]


# ---------- Cog ----------

def require_owner(interaction: discord.Interaction) -> None:
    if interaction.user.id != interaction.guild.owner_id:
        raise UserError("為了安全，只有伺服器擁有者能修改防炸群設定（避免被盜的管理員帳號把防護關掉）。")


class AntiNuke(commands.Cog):
    antinuke = app_commands.Group(
        name="antinuke", description="防炸群設定", guild_only=True,
        default_permissions=discord.Permissions(administrator=True))
    whitelist = app_commands.Group(name="whitelist", description="白名單（不受防炸群限制）", parent=antinuke)

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.actions = ActionTracker()
        self.messages = MessageTracker()
        self.cooldown: dict[tuple, float] = {}
        self.locks: dict[int, asyncio.Lock] = {}

    def lock(self, guild: discord.Guild) -> asyncio.Lock:
        return self.locks.setdefault(guild.id, asyncio.Lock())

    async def get_cfg(self, guild: discord.Guild) -> dict:
        return load_cfg((await self.bot.config.get(guild)).get("antinuke"))

    async def save_cfg(self, guild: discord.Guild, cfg: dict) -> None:
        await self.bot.config.update(guild, "antinuke", cfg)

    def is_trusted(self, guild: discord.Guild, user_id: int, cfg: dict) -> bool:
        return user_id in (guild.owner_id, self.bot.user.id) or str(user_id) in cfg["whitelist"]

    def start_handling(self, key: tuple) -> bool:
        """同一個人短時間內只處理一次，避免重複懲罰和日誌洗版。"""
        now = time.monotonic()
        if now - self.cooldown.get(key, -COOLDOWN) < COOLDOWN:
            return False
        self.cooldown[key] = now
        return True

    # ---------- 通知 ----------

    async def alert(self, guild: discord.Guild, cfg: dict, embed: discord.Embed, ping: bool = True) -> None:
        mentions = discord.AllowedMentions(users=True, roles=False, everyone=False)
        channel = guild.get_channel(cfg["log_channel"] or 0)
        if channel is not None:
            try:
                await channel.send(f"<@{guild.owner_id}>" if ping else None, embed=embed, allowed_mentions=mentions)
                return
            except discord.HTTPException:
                pass
        # 日誌頻道被刪或不能發言 → 改私訊擁有者
        try:
            owner = guild.owner or await self.bot.fetch_user(guild.owner_id)
            embed.set_footer(text=f"日誌頻道無法使用，改用私訊通知｜{guild.name}")
            await owner.send(embed=embed)
        except discord.HTTPException:
            log.warning("無法通知 %s 的擁有者：%s", guild.name, embed.description)

    # ---------- 處置 ----------

    async def punish(self, guild: discord.Guild, user_id: int, cfg: dict, reason: str) -> None:
        if not self.start_handling((guild.id, user_id)):
            return
        log.warning("防炸群觸發：%s 的 %s，%s", guild.name, user_id, reason)
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.HTTPException:
                e = discord.Embed(title="🚨 防炸群觸發", color=RED,
                                  description=f"<@{user_id}>（ID `{user_id}`）\n**原因：**{reason}")
                e.add_field(name="⚠️ 處置失敗", value="對方已經不在伺服器裡了。")
                await self.alert(guild, cfg, e)
                return

        e = discord.Embed(title="🚨 防炸群觸發", color=RED, description=f"{user_line(member)}\n**原因：**{reason}")
        if member.top_role >= guild.me.top_role:
            e.add_field(name="⚠️ 處置失敗",
                        value="對方的身分組比機器人高或一樣高，機器人動不了他！\n"
                              "請到「伺服器設定 → 身分組」把機器人的身分組拉到**最上面**，並立刻手動處理這個人。",
                        inline=False)
            await self.alert(guild, cfg, e)
            return
        try:
            if member.bot:
                await member.kick(reason=f"防炸群：{reason}")
                e.add_field(name="處置", value="👢 已踢出這個機器人（機器人自帶的身分組拿不掉，只能踢出）\n"
                                               "誤判的話請重新邀請它，並用 `/antinuke whitelist add` 加入白名單。",
                            inline=False)
            else:
                e.add_field(name="處置", value=await self.quarantine(member, cfg, reason), inline=False)
                e.add_field(name="誤判了嗎？", value=f"用 `/antinuke restore` 選 {member.mention} 就能還原身分組、解除禁言。",
                            inline=False)
        except discord.HTTPException as error:
            log.exception("處置 %s 失敗", member)
            e.add_field(name="⚠️ 處置失敗", value=f"請確認機器人有「管理員」權限，並立刻手動處理。（{error}）",
                        inline=False)
        await self.alert(guild, cfg, e)

    async def quarantine(self, member: discord.Member, cfg: dict, reason: str) -> str:
        guild = member.guild
        role = guild.get_role(cfg["quarantine_role"] or 0)
        removed = [r for r in member.roles if not r.is_default() and not r.managed and r != role]
        keep = [r for r in member.roles if r.managed]  # 加成者等系統身分組拿不掉，保留
        # 速度最重要：先拿掉身分組，之後再存檔
        await member.edit(roles=keep + ([role] if role else []), reason=f"防炸群：{reason}")

        lines = [f"🔒 拿掉 {len(removed)} 個身分組"]
        if removed:
            lines[0] += "：" + " ".join(r.mention for r in removed)[:800]
        lines.append(f"🔒 加上 {role.mention}（只看得到隔離區）" if role
                     else "⚠️ 找不到隔離身分組，請重新執行 `/antinuke setup`")
        try:
            await member.timeout(datetime.timedelta(minutes=cfg["timeout_minutes"]), reason=f"防炸群：{reason}")
            lines.append(f"🔇 禁言 {cfg['timeout_minutes']} 分鐘")
        except discord.HTTPException:
            lines.append("⚠️ 禁言失敗（@everyone 可能有管理員權限）")

        async with self.lock(guild):
            data = await self.get_cfg(guild)
            old = data["quarantined"].get(str(member.id), {}).get("roles", [])
            data["quarantined"][str(member.id)] = {
                "roles": list(dict.fromkeys(old + [str(r.id) for r in removed])),
                "at": int(time.time()), "reason": reason}
            await self.save_cfg(guild, data)
        return "\n".join(lines)

    async def stop_webhook(self, guild: discord.Guild, webhook_id: int, cfg: dict, reason: str) -> None:
        if not self.start_handling((guild.id, webhook_id)):
            return
        try:
            webhook = await self.bot.fetch_webhook(webhook_id)
        except discord.HTTPException:
            return
        if webhook.type is discord.WebhookType.channel_follower:  # 追蹤公告頻道，不處理
            return
        creator = webhook.user
        if creator is not None and self.is_trusted(guild, creator.id, cfg):
            return
        e = discord.Embed(title="🚨 防炸群觸發：Webhook 洗版", color=RED,
                          description=f"Webhook **{webhook.name}**（ID `{webhook.id}`）\n**原因：**{reason}")
        e.add_field(name="建立者", value=user_line(creator) if creator else "（未知）", inline=False)
        try:
            await webhook.delete(reason=f"防炸群：{reason}")
            e.add_field(name="處置", value="🗑️ 已刪除這個 Webhook\n建立者沒有被處罰，如果他很可疑請手動處理。",
                        inline=False)
        except discord.HTTPException as error:
            e.add_field(name="⚠️ 處置失敗", value=f"刪不掉這個 Webhook，請手動刪除。（{error}）", inline=False)
        await self.alert(guild, cfg, e)

    async def purge(self, items: list[tuple[int, int]]) -> None:
        by_channel: dict[int, list] = defaultdict(list)
        for channel_id, message_id in items:
            by_channel[channel_id].append(discord.Object(message_id))
        for channel_id, messages in by_channel.items():
            channel = self.bot.get_channel(channel_id)
            if not hasattr(channel, "delete_messages"):
                continue
            try:
                for i in range(0, len(messages), 100):
                    await channel.delete_messages(messages[i:i + 100], reason="防炸群：刪除洗版訊息")
            except discord.HTTPException:
                pass

    # ---------- 事件 ----------

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry: discord.AuditLogEntry):
        if entry.action is not discord.AuditLogAction.channel_delete or entry.user_id is None:
            return
        guild = entry.guild
        cfg = await self.get_cfg(guild)
        if not cfg["enabled"] or self.is_trusted(guild, entry.user_id, cfg):
            return
        count, seconds = cfg["limits"]["channel_delete"]
        if self.actions.hit((guild.id, entry.user_id), count, seconds):
            await self.punish(guild, entry.user_id, cfg, describe_rule("channel_delete", [count, seconds]))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        guild = message.guild
        if guild is None or message.author.id == self.bot.user.id:
            return
        # 只看一般訊息；斜線指令的回覆、系統訊息（加入、置頂…）不算
        if message.interaction_metadata is not None or \
                message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return
        cfg = await self.get_cfg(guild)
        if not cfg["enabled"]:
            return
        webhook_id = message.webhook_id
        if webhook_id is None and self.is_trusted(guild, message.author.id, cfg):
            return
        key = (guild.id, webhook_id or message.author.id)
        hit = self.messages.add(key, message.channel.id, message.id,
                                cfg["limits"]["msg_flood"], cfg["limits"]["msg_channels"])
        if hit is None:
            return
        reason = describe_rule(hit, cfg["limits"][hit])
        if webhook_id is not None:
            await self.stop_webhook(guild, webhook_id, cfg, reason)
        else:
            await self.punish(guild, message.author.id, cfg, reason)
        await self.purge(self.messages.pop(key))

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        """新頻道也要對隔離身分組隱藏。"""
        cfg = await self.get_cfg(channel.guild)
        role = channel.guild.get_role(cfg["quarantine_role"] or 0)
        if role is None:
            return
        if channel.overwrites_for(role).view_channel is None:  # 隔離區是「允許」，不會被改掉
            try:
                await channel.set_permissions(role, view_channel=False, reason="防炸群：隔離身分組看不到新頻道")
            except discord.HTTPException:
                pass

    # ---------- 指令 ----------

    def status_text(self, guild: discord.Guild, cfg: dict) -> str:
        log_channel = guild.get_channel(cfg["log_channel"] or 0)
        role = guild.get_role(cfg["quarantine_role"] or 0)
        rules = "\n".join(f"• {RULES[k]}：{describe_rule(k, cfg['limits'][k])}" for k in RULES)
        lines = [
            f"**狀態：**{'🟢 開啟' if cfg['enabled'] else '🔴 關閉'}",
            f"**日誌頻道：**{log_channel.mention if log_channel else '未設定'}",
            f"**隔離身分組：**{role.mention if role else '未設定'}",
            f"**觸發條件：**\n{rules}",
            f"**處置：**拿掉所有身分組 → 加上隔離身分組 → 禁言 {cfg['timeout_minutes']} 分鐘（機器人則直接踢出）",
            f"**白名單：**{' '.join(f'<@{u}>' for u in cfg['whitelist']) or '（無）'}（擁有者一定不受限制）",
            f"**目前隔離中：**{' '.join(f'<@{u}>' for u in cfg['quarantined']) or '（無）'}",
        ]
        if guild.me.top_role != max(guild.roles):
            lines.append("\n⚠️ **機器人的身分組不是最上面！**請到「伺服器設定 → 身分組」把它拉到最上面，"
                         "不然擋不住身分組比它高的人。")
        return "\n".join(lines)

    @antinuke.command(name="setup", description="開啟防炸群並建立隔離身分組和隔離區（只有擁有者能用）")
    @app_commands.describe(log_channel="防炸群觸發時要通知的頻道")
    async def setup(self, interaction: discord.Interaction, log_channel: discord.TextChannel):
        require_owner(interaction)
        guild = interaction.guild
        perms = guild.me.guild_permissions
        missing = [name for key, name in NEEDED_PERMS.items() if not getattr(perms, key)]
        if missing:
            raise UserError("機器人缺少這些權限：" + "、".join(missing) + "。建議直接給機器人「管理員」權限。")
        await interaction.response.defer(ephemeral=True, thinking=True)

        async with self.lock(guild):
            cfg = await self.get_cfg(guild)
            role = guild.get_role(cfg["quarantine_role"] or 0)
            if role is None:
                role = await guild.create_role(name=QUARANTINE_ROLE_NAME, permissions=discord.Permissions.none(),
                                               reason="防炸群：建立隔離身分組")
            q_channel = guild.get_channel(cfg["quarantine_channel"] or 0)
            if q_channel is None:
                q_channel = await guild.create_text_channel(
                    QUARANTINE_CHANNEL_NAME, topic="被防炸群隔離的成員只看得到這個頻道，管理員會在這裡跟你聯絡。",
                    overwrites={
                        guild.default_role: discord.PermissionOverwrite(view_channel=False),
                        role: discord.PermissionOverwrite(view_channel=True, read_message_history=True),
                        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
                    }, reason="防炸群：建立隔離區")

            # 先設分類再設頻道，這樣原本跟分類同步的頻道設完還是同步的
            failed = []
            channels = sorted(guild.channels, key=lambda c: not isinstance(c, discord.CategoryChannel))
            for channel in channels:
                if channel.id == q_channel.id or channel.overwrites_for(role).view_channel is not None:
                    continue
                try:
                    await channel.set_permissions(role, view_channel=False, reason="防炸群：隔離身分組看不到這裡")
                except discord.HTTPException:
                    failed.append(channel.mention)

            cfg.update(enabled=True, log_channel=log_channel.id, quarantine_role=role.id,
                       quarantine_channel=q_channel.id)
            await self.save_cfg(guild, cfg)

        text = "✅ 防炸群已開啟！\n\n" + self.status_text(guild, cfg)
        if failed:
            text += "\n\n⚠️ 這些頻道設定失敗，隔離中的人可能還看得到：" + " ".join(failed)[:500]
        text += ("\n\n📌 **提醒**\n• 其他會大量刪頻道或發訊息的機器人（例如你的開單機器人），"
                 "請用 `/antinuke whitelist add` 加入白名單\n"
                 "• 調整門檻：`/antinuke limit`；調整禁言時間：`/antinuke timeout`")
        await interaction.followup.send(text[:2000], ephemeral=True)

    @antinuke.command(name="status", description="查看防炸群目前的設定")
    async def status(self, interaction: discord.Interaction):
        cfg = await self.get_cfg(interaction.guild)
        await interaction.response.send_message(self.status_text(interaction.guild, cfg)[:2000], ephemeral=True)

    @antinuke.command(name="enable", description="開啟或關閉防炸群（只有擁有者能用）")
    @app_commands.describe(on="開啟還是關閉")
    @app_commands.choices(on=[app_commands.Choice(name="開啟", value=1), app_commands.Choice(name="關閉", value=0)])
    async def enable(self, interaction: discord.Interaction, on: app_commands.Choice[int]):
        require_owner(interaction)
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.lock(guild):
            cfg = await self.get_cfg(guild)
            if on.value and cfg["quarantine_role"] is None:
                raise UserError("請先用 `/antinuke setup` 完成初始設定。")
            cfg["enabled"] = bool(on.value)
            await self.save_cfg(guild, cfg)
        await interaction.followup.send(f"✅ 防炸群已{on.name}。", ephemeral=True)

    @antinuke.command(name="limit", description="調整觸發門檻（只有擁有者能用）")
    @app_commands.describe(rule="要調整哪一項", count="達到幾次就處理", seconds="在幾秒內")
    @app_commands.choices(rule=[app_commands.Choice(name=name, value=key) for key, name in RULES.items()])
    async def limit(self, interaction: discord.Interaction, rule: app_commands.Choice[str],
                    count: app_commands.Range[int, 2, 100], seconds: app_commands.Range[int, 1, 3600]):
        require_owner(interaction)
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.lock(guild):
            cfg = await self.get_cfg(guild)
            cfg["limits"][rule.value] = [count, seconds]
            await self.save_cfg(guild, cfg)
        await interaction.followup.send(
            f"✅ {rule.name}：改成「{describe_rule(rule.value, [count, seconds])}」就處理。", ephemeral=True)

    @antinuke.command(name="timeout", description="調整觸發後的禁言時間（只有擁有者能用）")
    @app_commands.describe(minutes="禁言幾分鐘（最多 40320 分鐘 = 28 天）")
    async def timeout(self, interaction: discord.Interaction, minutes: app_commands.Range[int, 1, MAX_TIMEOUT]):
        require_owner(interaction)
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.lock(guild):
            cfg = await self.get_cfg(guild)
            cfg["timeout_minutes"] = minutes
            await self.save_cfg(guild, cfg)
        await interaction.followup.send(f"✅ 觸發後會禁言 {minutes} 分鐘。", ephemeral=True)

    @whitelist.command(name="add", description="把信任的人或機器人加入白名單（只有擁有者能用）")
    @app_commands.describe(user="要加入白名單的成員或機器人")
    async def whitelist_add(self, interaction: discord.Interaction, user: discord.User):
        require_owner(interaction)
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.lock(guild):
            cfg = await self.get_cfg(guild)
            if str(user.id) not in cfg["whitelist"]:
                cfg["whitelist"].append(str(user.id))
                await self.save_cfg(guild, cfg)
        await interaction.followup.send(f"✅ 已把 {user.mention} 加入白名單，他不會被防炸群處理。", ephemeral=True)

    @whitelist.command(name="remove", description="把人從白名單移除（只有擁有者能用）")
    @app_commands.describe(user="要移除的成員或機器人")
    async def whitelist_remove(self, interaction: discord.Interaction, user: discord.User):
        require_owner(interaction)
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.lock(guild):
            cfg = await self.get_cfg(guild)
            if str(user.id) not in cfg["whitelist"]:
                raise UserError(f"{user.mention} 本來就不在白名單裡。")
            cfg["whitelist"].remove(str(user.id))
            await self.save_cfg(guild, cfg)
        await interaction.followup.send(f"✅ 已把 {user.mention} 從白名單移除。", ephemeral=True)

    @antinuke.command(name="restore", description="還原被防炸群隔離的人（擁有者或白名單才能用）")
    @app_commands.describe(member="要還原的成員")
    async def restore(self, interaction: discord.Interaction, member: discord.Member):
        guild = interaction.guild
        if interaction.user.id != guild.owner_id and \
                str(interaction.user.id) not in (await self.get_cfg(guild))["whitelist"]:
            raise UserError("只有伺服器擁有者或白名單裡的人能還原。")
        if member.top_role >= guild.me.top_role:
            raise UserError("對方的身分組比機器人高，請先把機器人的身分組拉到最上面。")
        await interaction.response.defer(ephemeral=True, thinking=True)

        async with self.lock(guild):
            cfg = await self.get_cfg(guild)
            record = cfg["quarantined"].get(str(member.id))
            q_role = cfg["quarantine_role"]
            if record is None and all(r.id != q_role for r in member.roles) and not member.is_timed_out():
                raise UserError(f"{member.mention} 沒有被隔離。")
            saved = [guild.get_role(int(r)) for r in (record or {}).get("roles", [])]
            back = [r for r in saved if r is not None and not r.managed and r < guild.me.top_role]
            current = [r for r in member.roles if not r.is_default() and r.id != q_role]
            await member.edit(roles=list(dict.fromkeys(current + back)), timed_out_until=None,
                              reason=f"防炸群：{interaction.user} 還原")
            cfg["quarantined"].pop(str(member.id), None)
            await self.save_cfg(guild, cfg)

        text = f"✅ 已還原 {member.mention}：加回 {len(back)} 個身分組、解除禁言。"
        if len(back) < len(saved):
            text += f"\n⚠️ 有 {len(saved) - len(back)} 個身分組加不回去（已被刪除，或排在機器人上面），請手動處理。"
        await interaction.followup.send(text, ephemeral=True)
        e = discord.Embed(title="↩️ 防炸群：已還原", color=GREEN,
                          description=f"{user_line(member)}\n由 {interaction.user.mention} 還原")
        await self.alert(guild, cfg, e, ping=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(AntiNuke(bot))
