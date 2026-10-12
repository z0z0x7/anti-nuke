import discord

from utils import send_error

COLOR = 0xE53935

# (分類名稱, 表情, 簡介, [(指令, 說明), ...])
SECTIONS = {
    "setup": ("開啟與設定", "⚙️", "第一次使用、開關防炸群、查看設定", [
        ("/antinuke setup log_channel:#日誌", "開啟防炸群，自動建立「🔒 隔離中」身分組和 #🔒隔離區。**第一次使用請先執行這個**"),
        ("/antinuke enable", "開啟或關閉防炸群"),
        ("/antinuke status", "查看目前設定、白名單、隔離中的人，並檢查機器人身分組是不是在最上面"),
    ]),
    "limits": ("觸發條件與處置", "🚨", "什麼情況會觸發、觸發後怎麼處理", [
        ("/antinuke limit rule count seconds", "調整觸發門檻，例如「60 秒內刪 3 個頻道」"),
        ("/antinuke timeout minutes", "調整觸發後禁言幾分鐘（最多 28 天）"),
    ]),
    "whitelist": ("白名單", "✅", "信任的管理員和機器人，不受防炸群限制", [
        ("/antinuke whitelist add user", "加入白名單。**會建頻道、刪頻道、大量發訊息的機器人一定要加**（例如開單機器人）"),
        ("/antinuke whitelist remove user", "從白名單移除"),
    ]),
    "restore": ("誤判還原", "↩️", "不小心處理到正常的人時怎麼救回來", [
        ("/antinuke restore member", "加回被拿掉的身分組、拿掉隔離身分組、解除禁言（擁有者或白名單才能用）"),
    ]),
}

FAQ = [
    ("觸發後會怎樣？",
     "• **成員**：拿掉所有身分組 → 加上「🔒 隔離中」（只看得到 #🔒隔離區）→ 禁言，並刪掉他剛剛洗版的訊息\n"
     "• **機器人**：直接踢出（機器人自帶的身分組拿不掉）\n"
     "• **Webhook 洗版**：刪除那個 Webhook\n"
     "每次都會發到日誌頻道並 @ 擁有者"),
    ("誰不會被處理？", "伺服器擁有者、白名單裡的人和機器人"),
    ("為什麼大部分設定只有擁有者能改？", "避免管理員帳號被盜時，對方先把防護關掉或把自己加進白名單"),
    ("機器人說「身分組比機器人高」？", "到「伺服器設定 → 身分組」把機器人的身分組**拖到最上面**，不然動不了比它高的人"),
    ("被刪掉的頻道會復原嗎？", "不會。日誌會列出被刪的頻道名稱，請手動重建；他亂建的頻道也要手動刪"),
    ("可以刪掉機器人建的東西嗎？", "不行，請保留「🔒 隔離中」身分組、#🔒隔離區 和 #防炸群-設定（設定存在這裡）"),
]


def overview_embed(rules_text: str, timeout_minutes: int, enabled: bool) -> discord.Embed:
    e = discord.Embed(title="🛡️ 防炸群指令說明",
                      description=f"**目前狀態：**{'🟢 開啟' if enabled else '🔴 關閉'}\n"
                                  "用下方選單切換分類查看詳細用法。", color=COLOR)
    for name, emoji, intro, cmds in SECTIONS.values():
        e.add_field(name=f"{emoji} {name}", value=f"{intro}\n`{len(cmds)}` 個指令", inline=True)
    e.add_field(name="📏 目前的觸發條件", value=f"{rules_text}\n觸發後禁言 {timeout_minutes} 分鐘", inline=False)
    return e


def section_embed(key: str) -> discord.Embed:
    name, emoji, intro, cmds = SECTIONS[key]
    e = discord.Embed(title=f"{emoji} {name}", description=intro, color=COLOR)
    for cmd, desc in cmds:
        e.add_field(name=f"`{cmd}`", value=desc, inline=False)
    return e


def faq_embed() -> discord.Embed:
    e = discord.Embed(title="❓ 常見問題", color=COLOR)
    for q, a in FAQ:
        e.add_field(name=q, value=a, inline=False)
    return e


class HelpView(discord.ui.View):
    def __init__(self, overview: discord.Embed):
        super().__init__(timeout=600)
        self.overview = overview
        options = [discord.SelectOption(label="總覽", value="overview", emoji="🏠", default=True)]
        options += [discord.SelectOption(label=name, value=key, emoji=emoji, description=intro[:100])
                    for key, (name, emoji, intro, _) in SECTIONS.items()]
        options.append(discord.SelectOption(label="常見問題", value="faq", emoji="❓",
                                            description="觸發後會怎樣、誤判怎麼辦"))
        self.select = discord.ui.Select(placeholder="選擇分類", options=options)
        self.select.callback = self.on_select
        self.add_item(self.select)

    async def on_select(self, interaction: discord.Interaction):
        key = self.select.values[0]
        for option in self.select.options:
            option.default = option.value == key
        if key == "overview":
            embed = self.overview
        elif key == "faq":
            embed = faq_embed()
        else:
            embed = section_embed(key)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_error(self, interaction, error, _item):
        await send_error(interaction, error)
