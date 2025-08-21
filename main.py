import discord
from discord.ui import View, Modal, InputText, Button
import json
import gspread
import os
from dotenv import load_dotenv
import datetime
import pytz

# --- 設定項目 ---
load_dotenv()
GUILD_IDS = [int(id_str) for id_str in os.getenv("GUILD_IDS", "").split(',') if id_str]
TARGET_CHANNEL_ID = int(os.getenv("TARGET_CHANNEL_ID", 0))
TARGET_MESSAGE_ID = int(os.getenv("TARGET_MESSAGE_ID", 0))
SPREADSHEET_NAME = "グラナドエスパダM 党員所持リスト"
# ----------------

# --- Googleスプレッドシート連携 ---
spreadsheet = None
worksheet = None
CATEGORIES = []
try:
    # 環境変数から認証情報を文字列として取得
    creds_json_str = os.getenv("GCP_CREDENTIALS_JSON")
    if not creds_json_str:
        raise ValueError("環境変数 GCP_CREDENTIALS_JSON が設定されていません。")
    
    # 文字列を辞書に変換
    creds_dict = json.loads(creds_json_str)
    # ファイルではなく辞書を使って認証
    gc = gspread.service_account_from_dict(creds_dict)
    
    spreadsheet = gc.open(SPREADSHEET_NAME)
    worksheet = spreadsheet.worksheet("BOT書き込み用")
    print("スプレッドシート「BOT書き込み用」への接続に成功しました。")
    
    # キャラクターリストをスプレッドシートから読み込む
    char_worksheet = spreadsheet.worksheet("キャラクターリスト")
    all_names = char_worksheet.col_values(1)
    if len(all_names) > 1:
        CATEGORIES = all_names
    print(f"{len(CATEGORIES)} 件のキャラクターをスプレッドシートから読み込みました。")

except Exception as e:
    print(f"スプレッドシートへの接続・読み込み中にエラーが発生しました: {e}")
# --------------------------------

MODAL_GROUP_SIZE = 5
bot = discord.Bot()

# --- Embed生成関数 (閲覧専用) ---
def create_checklist_embed(all_items):
    embed = discord.Embed(title="共有チェックリスト（閲覧用）", color=discord.Color.blue())
    embed.set_footer(text="レベルを更新するには /bulk_update コマンドを使用してください。")
    
    if not all_items:
        embed.description = "まだ誰もキャラクターを登録していません。"
        return embed
    
    # 1. キャラクター名でデータをグループ化
    grouped_data = {}
    for item in all_items:
        char_name = item.get('キャラクター名', '不明')
        if char_name not in grouped_data:
            grouped_data[char_name] = []
        grouped_data[char_name].append(item)
    
    # 2. キャラクター名をソート
    sorted_char_names = sorted(grouped_data.keys())
    
    # 3. フィールドごとに文字列を組み立て、4096文字制限に対応
    field_value = ""
    field_count = 1
    for char_name in sorted_char_names:
        # 現在のキャラクター情報を文字列として作成
        char_block = f"**・{char_name}**\n"
        holders = sorted(grouped_data[char_name], key=lambda x: x.get('追加者', ''))
        for holder in holders:
            author = holder.get('追加者', '不明')
            level = holder.get('レベル', 'N/A')
            char_block += f"　所持者: {author} \t Lv. {level}\n"
        
        # 文字数制限のチェック (Discordのフィールド上限は1024文字)
        if len(field_value) + len(char_block) > 1024:
            # 制限を超える場合は、現在の内容でフィールドを追加し、新しいフィールドを開始
            embed.add_field(name=f"リスト ({field_count})", value=field_value, inline=False)
            field_value = char_block
            field_count += 1
        else:
            # 制限内であれば、現在のフィールドに追記
            field_value += char_block

    # ループ終了後、残っている内容を最後のフィールドとして追加
    if field_value:
        embed.add_field(name=f"リスト ({field_count})", value=field_value, inline=False)

    return embed

# --- 一括更新用Modal & View ---
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
            except Exception as e:
                print(f"データ読み込みエラー: {e}")

        for char_name in self.characters:
            current_level = user_items.get(char_name, "")
            self.add_item(InputText(
                label=char_name,
                placeholder=f"現在のレベル: {current_level}" if current_level else "未登録",
                custom_id=char_name,
                required=False
            ))

    async def callback(self, interaction: discord.Interaction):
        if not spreadsheet:
            await interaction.response.send_message("スプレッドシートに接続できていません。", ephemeral=True); return

        try:
            all_data = worksheet.get_all_records()
            updated_count = 0
            batch_update_requests = []

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
                        worksheet.append_row([char_name, new_level, self.author_name])
                    updated_count += 1
            
            if batch_update_requests:
                worksheet.batch_update(batch_update_requests)

            response_message = f"{updated_count}件の情報を更新しました。" if updated_count > 0 else "更新するレベルが入力されませんでした。"
            await interaction.response.send_message(response_message, ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"スプレッドシート更新中にエラーが発生: {e}", ephemeral=True)

class GroupSelectionView(View):
    def __init__(self, author_name: str, original_message=None):
        super().__init__(timeout=180)
        self.author_name = author_name
        self.original_message = original_message # bulk_updateからのメッセージを保持
        self.current_page = 0
        
        self.category_chunks = [CATEGORIES[i:i + MODAL_GROUP_SIZE] for i in range(0, len(CATEGORIES), MODAL_GROUP_SIZE)]
        self.total_pages = len(self.category_chunks)

        self.update_buttons()

    def update_buttons(self):
        self.clear_items()
        start_index = self.current_page * 4
        end_index = start_index + 4
        
        for i, chunk in enumerate(self.category_chunks[start_index:end_index]):
            self.add_item(Button(
                label=f"グループ {start_index + i + 1} ({chunk[0]}～)",
                style=discord.ButtonStyle.secondary,
                custom_id=f"group_select_{start_index + i}"
            ))

        if self.current_page > 0:
            self.add_item(Button(label="◀️ 前へ", style=discord.ButtonStyle.primary, custom_id="prev_page"))
        
        # ページの計算を修正
        if (self.current_page + 1) * 4 < len(self.category_chunks):
            self.add_item(Button(label="次へ ▶️", style=discord.ButtonStyle.primary, custom_id="next_page"))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        custom_id = interaction.data.get("custom_id")

        if custom_id == "prev_page":
            if self.current_page > 0:
                self.current_page -= 1
                self.update_buttons()
                await interaction.response.edit_message(view=self)
            return False

        if custom_id == "next_page":
            self.current_page += 1
            self.update_buttons()
            await interaction.response.edit_message(view=self)
            return False

        if custom_id and custom_id.startswith("group_select"):
            group_index = int(custom_id.split('_')[-1])
            selected_chunk = self.category_chunks[group_index]
            
            # ↓↓↓ ここから 'original_message' を渡さないように修正 ↓↓↓
            modal = BulkUpdateModal(
                characters_to_update=selected_chunk,
                author_name=self.author_name,
            )
            await interaction.response.send_modal(modal)
            return False
            
        return True
        
    JST = pytz.timezone('Asia/Tokyo')
    
    def calculate_next_fb(base_time_str: str, interval_hours: int) -> datetime.datetime:
        """次のFB時間を計算する関数"""
        now = datetime.datetime.now(JST)
        
        # 今日の日付で基準時間を作成
        today_base_time = JST.localize(datetime.datetime.strptime(f"{now.strftime('%Y-%m-%d')} {base_time_str}", "%Y-%m-%d %H:%M"))
        
        # 基準となる過去の時間を探す
        time_diff_seconds = (now - today_base_time).total_seconds()
        interval_seconds = interval_hours * 3600
        
        cycles_ago = time_diff_seconds / interval_seconds
        
        most_recent_base_time = today_base_time + datetime.timedelta(seconds=int(cycles_ago) * interval_seconds)
        
        if most_recent_base_time > now:
            most_recent_base_time -= datetime.timedelta(seconds=interval_seconds)
    
        next_time = most_recent_base_time + datetime.timedelta(seconds=interval_seconds)
        
        return next_time

# --- コマンド定義 ---
@bot.event
async def on_ready():
    print(f"{bot.user}としてログインしました")
    print("--- 環境変数の読み込み状態チェック ---")
    print(f"TARGET_CHANNEL_ID: {os.getenv('TARGET_CHANNEL_ID')}")
    print(f"TARGET_MESSAGE_ID: {os.getenv('TARGET_MESSAGE_ID')}")
    print("---------------------------------")

@bot.slash_command(description="スプレッドシートの最新の全体状況を表示します。", guild_ids=GUILD_IDS)
async def checklist(ctx):
    if not spreadsheet:
        await ctx.respond("スプレッドシートに接続できていません。", ephemeral=True); return
    try:
        all_items = worksheet.get_all_records()
        embed = create_checklist_embed(all_items)
        await ctx.respond(embed=embed)
    except Exception as e:
        await ctx.respond(f"リスト表示中にエラーが発生: {e}", ephemeral=True)

@bot.slash_command(description="複数のキャラクターのレベルを一度に更新します。", guild_ids=GUILD_IDS)
async def bulk_update(ctx):
    if not CATEGORIES:
        await ctx.respond("キャラクターリストがスプレッドシートから読み込めていません。", ephemeral=True)
        return
        
    response = await ctx.respond("更新したいキャラクターのグループを選択してください。", view=GroupSelectionView(author_name=ctx.author.display_name), ephemeral=True)
    message = await response.original_response()

    view = GroupSelectionView(author_name=ctx.author.display_name, original_message=message)
    await response.edit(view=view)

@bot.slash_command(description="自分が登録した内容をスプレッドシートから表示します。", guild_ids=GUILD_IDS)
async def my_list(ctx):
    if not spreadsheet:
        await ctx.respond("スプレッドシートに接続できていません。", ephemeral=True); return
    try:
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
        await ctx.respond(embed=embed, ephemeral=True)
    except Exception as e:
        await ctx.respond(f"リスト表示中にエラーが発生: {e}", ephemeral=True)

# main.py の中の /search コマンドを置き換える

@bot.slash_command(description="指定したキャラクターの所持者とレベルの一覧を表示します。", guild_ids=GUILD_IDS)
async def search(
    ctx,
    # ↓↓↓ choices=CATEGORIES の部分を削除しました ↓↓↓
    キャラクター名: discord.Option(str, "検索したいキャラクターの名前を入力してください")
):
    if not spreadsheet:
        await ctx.respond("スプレッドシートに接続できていません。", ephemeral=True)
        return

    try:
        # スプレッドシートから全データを取得
        all_data = worksheet.get_all_records()

        # 入力されたキャラクター名でデータを絞り込む (完全一致)
        filtered_items = [row for row in all_data if row.get('キャラクター名') == キャラクター名]
        
        # Embedを作成して結果を表示
        embed = discord.Embed(
            title=f"「{キャラクター名}」の検索結果",
            color=discord.Color.purple()
        )

        if not filtered_items:
            embed.description = "このキャラクターを登録している人はいません。"
        else:
            description = ""
            # 追加者名でソートして表示
            sorted_items = sorted(filtered_items, key=lambda x: x.get('追加者', ''))
            for item in sorted_items:
                author = item.get('追加者', '不明')
                level = item.get('レベル', 'N/A')
                description += f"所持者: {author} \t Lv. {level}\n"
            embed.description = description
            
        await ctx.respond(embed=embed)

    except Exception as e:
        await ctx.respond(f"検索中にエラーが発生しました: {e}", ephemeral=True)

# コマンドが間違ったチャンネルで使われたときのための、カスタムエラーを定義
class WrongChannelError(discord.CheckFailure):
    pass
    
@bot.before_invoke
async def check_channel(ctx: discord.ApplicationContext):
    """Checks if the command is used in the designated channel before execution."""
    # Get the target channel ID from the environment variables
    target_channel_id = int(os.getenv("TARGET_CHANNEL_ID", 0))

    if target_channel_id != 0 and ctx.channel.id != target_channel_id:
        raise WrongChannelError("This command can only be used in the designated channel.")

@bot.event
async def on_application_command_error(ctx: discord.ApplicationContext, error: discord.DiscordException):
    """Handles errors that occur during command execution."""
    if isinstance(error, WrongChannelError):
        await ctx.respond("This command can only be used in the designated channel.", ephemeral=True)
    else:
        # For other errors, log them to the console
        print(f"An unhandled error occurred in command {ctx.command.name}: {error}")
        # Optionally, send a generic error message to the user
        try:
            await ctx.respond("An unexpected error occurred.", ephemeral=True)
        except discord.errors.InteractionResponded:
            # If we already responded, we can follow up
            await ctx.followup.send("An unexpected error occurred.", ephemeral=True)

@bot.slash_command(description="次のコインブラFBの時間を通知します。", guild_ids=GUILD_IDS)
async def coinbra_fb(ctx):
    # 初回20時、10時間周期
    next_fb_time = calculate_next_fb("20:00", 10)
    await ctx.respond(f"次のコインブラFBは **{next_fb_time.strftime('%m月%d日 %H時%M分')}** です。")

@bot.slash_command(description="次のオーシュFBの時間を通知します。", guild_ids=GUILD_IDS)
async def oshu_fb(ctx):
    # 初回22時、21時間周期
    next_fb_time = calculate_next_fb("22:00", 21)
    await ctx.respond(f"次のオーシュFBは **{next_fb_time.strftime('%m月%d日 %H時%M分')}** です。")

# .env読み込みとBot起動
load_dotenv()
bot.run(os.getenv("DISCORD_TOKEN"))













