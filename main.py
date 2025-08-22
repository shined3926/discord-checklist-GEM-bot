import discord
from discord.ui import View, Modal, InputText, Button
import json
import gspread
import os
from dotenv import load_dotenv

# --- 設定項目 ---
load_dotenv()
GUILD_IDS = [int(id_str) for id_str in os.getenv("GUILD_IDS", "").split(',') if id_str]
SPREADSHEET_NAME = "グラナ-ドエスパダM 党員所持リスト"
TARGET_CHANNEL_ID = int(os.getenv("TARGET_CHANNEL_ID", 0))
# ----------------

# --- Googleスプレッドシート連携 ---
spreadsheet = None
worksheet = None
CATEGORIES = []
try:
    creds_json_str = os.getenv("GCP_CREDENTIALS_JSON")
    if not creds_json_str:
        raise ValueError("環境変数 GCP_CREDENTIALS_JSON が設定されていません。")
    creds_dict = json.loads(creds_json_str)
    gc = gspread.service_account_from_dict(creds_dict)
    spreadsheet = gc.open(SPREADSHEET_NAME)
    worksheet = spreadsheet.worksheet("BOT書き込み用")
    print("スプレッドシート「BOT書き込み用」への接続に成功しました。")
    char_worksheet = spreadsheet.worksheet("キャラクターリスト")
    all_names = char_worksheet.col_values(1)
    if all_names:
        CATEGORIES = all_names
    print(f"{len(CATEGORIES)} 件のキャラクターをスプレッドシートから読み込みました。")
except Exception as e:
    print(f"スプレッドシートへの接続・読み込み中にエラーが発生しました: {e}")
# --------------------------------

MODAL_GROUP_SIZE = 5
bot = discord.Bot()

# --- Embed生成関数 ---
def create_checklist_embed(all_items):
    embed = discord.Embed(title="共有チェックリスト（閲覧用）", color=discord.Color.blue())
    embed.set_footer(text="レベルを更新するには /bulk_update コマンドを使用してください。")
    if not all_items:
        embed.description = "まだ誰もキャラクターを登録していません。"
        return embed
    
    grouped_data = {}
    for item in all_items:
        char_name = item.get('キャラクター名', '不明')
        if char_name not in grouped_data: grouped_data[char_name] = []
        grouped_data[char_name].append(item)
    
    sorted_char_names = sorted(grouped_data.keys())
    
    field_value = ""
    field_count = 1
    for char_name in sorted_char_names:
        char_block = f"**・{char_name}**\n"
        holders = sorted(grouped_data[char_name], key=lambda x: x.get('追加者', ''))
        for holder in holders:
            char_block += f"　所持者: {holder.get('追加者', '不明')} \t Lv. {holder.get('レベル', 'N/A')}\n"
        
        if len(field_value) + len(char_block) > 1024:
            embed.add_field(name=f"リスト ({field_count})", value=field_value, inline=False)
            field_value = char_block
            field_count += 1
        else:
            field_value += char_block
    if field_value:
        embed.add_field(name=f"リスト ({field_count})", value=field_value, inline=False)
    return embed

# --- UIクラス ---
class BulkUpdateModal(Modal):
    def __init__(self, characters_to_update: list, author_name: str):
        super().__init__(title="キャラクターレベルの一括更新")
        self.characters = characters_to_update
        self.author_name = author_name
        user_items = {}
        if spreadsheet:
            try:
                all_data = worksheet.get_all_records()
                user_items = {row['キャラクター名']: row['レベル'] for row in all_data if row.get('追加者') == self.author_name}
            except Exception as e: print(f"データ読み込みエラー: {e}")
        for char_name in self.characters:
            current_level = user_items.get(char_name, "")
            self.add_item(InputText(label=char_name, placeholder=f"現在のレベル: {current_level}" if current_level else "未登録", custom_id=char_name, required=False))

    async def callback(self, interaction: discord.Interaction):
        if not spreadsheet:
            await interaction.response.send_message("スプレッドシートに接続できません。", ephemeral=True); return
        try:
            all_data = worksheet.get_all_records()
            updated_count = 0
            batch_update_requests = []
            new_rows = []
            for field in self.children:
                if field.value:
                    char_name, new_level = field.custom_id, field.value
                    row_to_update = -1
                    for i, row in enumerate(all_data):
                        if row.get('キャラクター名') == char_name and row.get('追加者') == self.author_name:
                            row_to_update = i + 2; break
                    if row_to_update != -1:
                        batch_update_requests.append({'range': f'B{row_to_update}', 'values': [[new_level]]})
                    else:
                        new_rows.append([char_name, new_level, self.author_name])
                    updated_count += 1
            if batch_update_requests: worksheet.batch_update(batch_update_requests)
            if new_rows: worksheet.append_rows(new_rows)
            response_message = f"{updated_count}件の情報を更新しました。" if updated_count > 0 else "更新するレベルが入力されませんでした。"
            await interaction.response.send_message(response_message, ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"スプレッドシート更新中にエラーが発生: {e}", ephemeral=True)

class GroupSelectionView(View):
    def __init__(self):
        super().__init__(timeout=180)
        category_chunks = [CATEGORIES[i:i + MODAL_GROUP_SIZE] for i in range(0, len(CATEGORIES), MODAL_GROUP_SIZE)]
        for i, chunk in enumerate(category_chunks[:5]):
            self.add_item(Button(label=f"グループ {i+1} ({chunk[0]}～)", style=discord.ButtonStyle.secondary, custom_id=f"group_select_{i}"))
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        custom_id = interaction.data.get("custom_id")
        if custom_id and custom_id.startswith("group_select"):
            group_index = int(custom_id.split('_')[-1])
            category_chunks = [CATEGORIES[i:i + MODAL_GROUP_SIZE] for i in range(0, len(CATEGORIES), MODAL_GROUP_SIZE)]
            modal = BulkUpdateModal(characters_to_update=category_chunks[group_index], author_name=interaction.user.display_name)
            await interaction.response.send_modal(modal); return False
        return True

# --- コマンド & イベント定義 ---
@bot.event
async def on_ready():
    print(f"{bot.user}としてログインしました")

class WrongChannelError(discord.CheckFailure): pass

@bot.before_invoke
async def check_channel(ctx: discord.ApplicationContext):
    if TARGET_CHANNEL_ID != 0 and ctx.channel.id != TARGET_CHANNEL_ID:
        raise WrongChannelError()

@bot.slash_command(description="スプレッドシートの最新の全体状況を、自分だけに表示します。", guild_ids=GUILD_IDS)
async def checklist(ctx):
    await ctx.defer(ephemeral=True) # ★ ephemeral=True に変更
    if not spreadsheet:
        await ctx.followup.send("スプレッドシートに接続できていません。", ephemeral=True); return
    all_items = worksheet.get_all_records()
    embed = create_checklist_embed(all_items)
    await ctx.followup.send(embed=embed)

@bot.slash_command(description="複数のキャラクターのレベルを一度に登録・更新します。", guild_ids=GUILD_IDS)
async def bulk_update(ctx):
    await ctx.defer(ephemeral=True)
    if not CATEGORIES:
        await ctx.followup.send("キャラクターリストが読み込めていません。", ephemeral=True); return
    await ctx.followup.send("更新したいキャラクターのグループを選択してください。", view=GroupSelectionView())

@bot.slash_command(description="自分が登録した内容をスプレッドシートから表示します。", guild_ids=GUILD_IDS)
async def my_list(ctx):
    await ctx.defer(ephemeral=True)
    if not spreadsheet:
        await ctx.followup.send("スプレッドシートに接続できていません。", ephemeral=True); return
    all_data = worksheet.get_all_records()
    author_name = ctx.author.display_name
    my_items = [row for row in all_data if row.get('追加者') == author_name]
    embed = discord.Embed(title=f"{author_name}さんの登録キャラクター一覧", color=discord.Color.green())
    if not my_items:
        embed.description = "あなたが登録したキャラクターは見つかりませんでした。"
    else:
        description = ""
        sorted_items = sorted(my_items, key=lambda x: x.get('キャラクター名', ''))
        for item in sorted_items:
            description += f"{item['キャラクター名']}: Lv. {item['レベル']}\n"
        embed.description = description
    await ctx.followup.send(embed=embed)

@bot.slash_command(description="指定したキャラクターの所持者とレベルの一覧を表示します。", guild_ids=GUILD_IDS)
async def search(ctx, キャラクター名: discord.Option(str, "検索したいキャラクターの名前を入力してください")):
    await ctx.defer()
    if not spreadsheet:
        await ctx.followup.send("スプレッドシートに接続できていません。", ephemeral=True); return
    all_data = worksheet.get_all_records()
    filtered_items = [row for row in all_data if row.get('キャラクター名') == キャラクター名]
    embed = discord.Embed(title=f"「{キャラクター名}」の検索結果", color=discord.Color.purple())
    if not filtered_items:
        embed.description = "このキャラクターを登録している人はいません。"
    else:
        description = ""
        sorted_items = sorted(filtered_items, key=lambda x: x.get('追加者', ''))
        for item in sorted_items:
            description += f"所持者: {item.get('追加者', '不明')} \t Lv. {item.get('レベル', 'N/A')}\n"
        embed.description = description
    await ctx.followup.send(embed=embed)

@bot.event
async def on_application_command_error(ctx: discord.ApplicationContext, error: discord.DiscordException):
    if isinstance(error, WrongChannelError):
        await ctx.respond("このコマンドは指定されたチャンネルでのみ使用できます。", ephemeral=True)
    else:
        # defer()済みの場合に備えてfollowupで応答
        await ctx.followup.send(f"コマンド実行中に予期せぬエラーが発生しました。管理者にご確認ください。", ephemeral=True)
        print(f"コマンド {ctx.command.name} でエラーが発生: {error}")

# .env読み込みとBot起動
bot.run(os.getenv("DISCORD_TOKEN"))
