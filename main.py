import discord
from discord.ui import View, Modal, InputText, Select, Button
import json
import gspread
import os
from dotenv import load_dotenv
import datetime
import pytz
import random
import requests
from discord.ext import tasks


# --- 設定項目 ---
load_dotenv()
GUILD_IDS = [int(id_str) for id_str in os.getenv("GUILD_IDS", "").split(',') if id_str]
SPREADSHEET_NAME = "グラナドエスパダM 党員所持リスト"
INFO_SPREADSHEET_NAME = os.getenv("INFO_SPREADSHEET_NAME", "グラナドエスパダM_BOT用DB") # .envから読み込む
TARGET_CHANNEL_ID = int(os.getenv("TARGET_CHANNEL_ID", 0))
# ----------------


# --- Googleスプレッドシート連携 ---
spreadsheet = None
worksheet = None
info_worksheet = None
CATEGORIES = []
CHAR_INFO_CATEGORIES = []
try:
    creds_json_str = os.getenv("GCP_CREDENTIALS_JSON")
    if not creds_json_str: raise ValueError("環境変数 GCP_CREDENTIALS_JSON が設定されていません。")
    creds_dict = json.loads(creds_json_str)
    gc = gspread.service_account_from_dict(creds_dict)
    
    # 1つ目のシート
    spreadsheet = gc.open(SPREADSHEET_NAME)
    worksheet = spreadsheet.worksheet("BOT書き込み用")
    print("スプレッドシート「BOT書き込み用」への接続に成功しました。")
    char_worksheet = spreadsheet.worksheet("キャラクターリスト")
    all_names = char_worksheet.col_values(1)
    if all_names: CATEGORIES = all_names
    print(f"{len(CATEGORIES)} 件のキャラクターをスプレッドシートから読み込みました。")

    # 2つ目のシート
    if INFO_SPREADSHEET_NAME:
        info_spreadsheet = gc.open(INFO_SPREADSHEET_NAME)
        info_worksheet = info_spreadsheet.worksheet("キャラクター一覧")
        print(f"2つ目のスプレッドシート「{INFO_SPREADSHEET_NAME}」への接続に成功しました。")
        if len(info_worksheet.col_values(1)) > 1:
            CHAR_INFO_CATEGORIES = info_worksheet.col_values(1)[1:]

except Exception as e:
    print(f"スプレッドシートへの接続・読み込み中にエラーが発生しました: {e}")
# ------------------------------------

# --- 天気予報機能 ---
# 気象庁APIで定義されている都道府県コード
PREFECTURE_CODES = {
    "北海道": "016000", "青森": "020000", "岩手": "030000", "宮城": "040000",
    "秋田": "050000", "山形": "060000", "福島": "070000", "茨城": "080000",
    "栃木": "090000", "群馬": "100000", "埼玉": "110000", "千葉": "120000",
    "東京": "130000", "神奈川": "140000", "新潟": "150000", "富山": "160000",
    "石川": "170000", "福井": "180000", "山梨": "190000", "長野": "200000",
    "岐阜": "210000", "静岡": "220000", "愛知": "230000", "三重": "240000",
    "滋賀": "250000", "京都": "260000", "大阪": "270000", "兵庫": "280000",
    "奈良": "290000", "和歌山": "300000", "鳥取": "310000", "島根": "320000",
    "岡山": "330000", "広島": "340000", "山口": "350000", "徳島": "360000",
    "香川": "370000", "愛媛": "380000", "高知": "390000", "福岡": "400000",
    "佐賀": "410000", "長崎": "420000", "熊本": "430000", "大分": "440000",
    "宮崎": "450000", "鹿児島": "460100", "沖縄": "471000"
}


MODAL_GROUP_SIZE = 5
bot = discord.Bot()

# --- Embed生成関数 ---
def create_checklist_embed(all_items):
    embed = discord.Embed(title="共有チェックリスト", color=discord.Color.blue())
    embed.set_footer(text="レベル更新は /bulk_update または下のメニューから行えます。")
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
class AddItemModal(Modal):
    def __init__(self, category: str, author_name: str):
        super().__init__(title=f"{category} のレベル入力")
        self.category = category
        self.author_name = author_name
        self.add_item(InputText(label="レベル", placeholder="例：90"))

    async def callback(self, interaction: discord.Interaction):
        if not spreadsheet:
            await interaction.response.send_message("スプレッドシートに接続できません。", ephemeral=True); return
        try:
            new_level = self.children[0].value
            all_data = worksheet.get_all_records()
            row_to_update = -1
            for i, row in enumerate(all_data):
                if row.get('キャラクター名') == self.category and row.get('追加者') == self.author_name:
                    row_to_update = i + 2; break
            
            if row_to_update != -1:
                worksheet.update_cell(row_to_update, 2, new_level)
                response_message = f"`{self.category}` のレベルを `{new_level}` に更新しました。"
            else:
                worksheet.append_row([self.category, new_level, self.author_name])
                response_message = f"`{self.category}` をレベル `{new_level}` で追加しました。"
            
            await interaction.response.send_message(response_message, ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"更新中にエラーが発生: {e}", ephemeral=True)

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
            batch_update_requests = []; new_rows = []
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
        super().__init__(timeout=None)
        self.current_page = 0
        
        self.category_chunks = [CATEGORIES[i:i + MODAL_GROUP_SIZE] for i in range(0, len(CATEGORIES), MODAL_GROUP_SIZE)]
        self.total_pages = -(-len(self.category_chunks) // 4)

        self.update_buttons()

    def update_buttons(self):
        """現在のページに基づいてボタンを再描画します"""
        self.clear_items()

        # 現在のページのグループボタンを追加します
        start_index = self.current_page * 4
        end_index = start_index + 4
        
        for i, chunk in enumerate(self.category_chunks[start_index:end_index]):
            self.add_item(Button(
                label=f"グループ {start_index + i + 1} ({chunk[0]}～)",
                style=discord.ButtonStyle.secondary,
                custom_id=f"group_select_{start_index + i}"
            ))

        # ページ送りボタンを一番下の行（4番目の行）に追加します
        if self.current_page > 0:
            self.add_item(Button(label="◀️ 前へ", style=discord.ButtonStyle.primary, custom_id="prev_page", row=4))
        
        if self.current_page < self.total_pages - 1:
            self.add_item(Button(label="次へ ▶️", style=discord.ButtonStyle.primary, custom_id="next_page", row=4))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        custom_id = interaction.data.get("custom_id")

        if custom_id == "prev_page":
            if self.current_page > 0:
                self.current_page -= 1
                self.update_buttons()
                await interaction.response.edit_message(view=self)
            return False

        if custom_id == "next_page":
            if self.current_page < self.total_pages - 1:
                self.current_page += 1
                self.update_buttons()
                await interaction.response.edit_message(view=self)
            return False

        if custom_id and custom_id.startswith("group_select"):
            group_index = int(custom_id.split('_')[-1])
            selected_chunk = self.category_chunks[group_index]
            
            modal = BulkUpdateModal(
                characters_to_update=selected_chunk,
                author_name=interaction.user.display_name,
            )
            await interaction.response.send_modal(modal)
            return False
            
        return True


class ChecklistView(View):
    def __init__(self):
        super().__init__(timeout=None)
        chunk_size = 25
        category_chunks = [CATEGORIES[i:i + chunk_size] for i in range(0, len(CATEGORIES), chunk_size)]
        for i, chunk in enumerate(category_chunks[:5]):
            options = [discord.SelectOption(label=cat) for cat in chunk]
            self.add_item(Select(placeholder=f"個別更新 ({i*chunk_size+1}～)...", options=options, custom_id=f"category_select_{i}"))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        custom_id = interaction.data.get("custom_id")
        if custom_id and custom_id.startswith("category_select"):
            category = interaction.data["values"][0]
            author = interaction.user.display_name
            modal = AddItemModal(category=category, author_name=author)
            await interaction.response.send_modal(modal)
            return False
        return True

# --- FB時間通知機能 ---
JST = pytz.timezone('Asia/Tokyo')
def calculate_next_fb(base_datetime_str: str, interval_hours: int) -> datetime.datetime:
    now = datetime.datetime.now(JST)
    base_time = JST.localize(datetime.datetime.strptime(base_datetime_str, "%Y/%m/%d %H:%M"))
    if base_time > now: return base_time
    time_diff_seconds = (now - base_time).total_seconds()
    interval_seconds = interval_hours * 3600
    cycles_passed = time_diff_seconds // interval_seconds
    next_time = base_time + datetime.timedelta(seconds=(cycles_passed + 1) * interval_seconds)
    return next_time

# --- 定期ダンジョン通知機能 ---
JST = pytz.timezone('Asia/Tokyo')
@tasks.loop(minutes=1) # 1分ごとにこの関数を実行する
async def dungeon_reminder():
    now = datetime.datetime.now(JST)
    weekday = now.weekday() # 曜日を取得 (月曜日=0, 日曜日=6)
    hour = now.hour
    minute = now.minute
    # 通知を送信するチャンネルを取得
    channel = bot.get_channel(TARGET_CHANNEL_ID)
    if not channel:
        return # チャンネルが見つからなければ何もしない
    # 金曜日(4) または 土曜日(5) の 20:00
    if (weekday == 4 or weekday == 5) and hour == 20 and minute == 0:
        await channel.send("【定期ダンジョン通知】\n日曜日21時から定期開催の党ダンジョンがあります！")
    # 日曜日(6) の 20:00
    if weekday == 6 and hour == 20 and minute == 0:
        await channel.send("【定期ダンジョン通知】\nこの後1時間後から定期開催の党ダンジョンが始まります！")

# --- コマンド & イベント定義 ---
@bot.event
async def on_ready():
    print(f"{bot.user}としてログインしました")
        if not dungeon_reminder.is_running():
        dungeon_reminder.start() 
    bot.add_view(ChecklistView())
    bot.add_view(GroupSelectionView()) 
class WrongChannelError(discord.CheckFailure): pass

@bot.event
async def on_close():
    if dungeon_reminder.is_running():
        dungeon_reminder.cancel() # Bot終了時にタスクを安全に停止

@bot.before_invoke
async def check_channel(ctx: discord.ApplicationContext):
    if TARGET_CHANNEL_ID != 0 and ctx.channel.id != TARGET_CHANNEL_ID:
        raise WrongChannelError()

@bot.slash_command(description="スプレッドシートの最新状況を自分だけに表示します。", guild_ids=GUILD_IDS)
async def checklist(ctx):
    await ctx.defer(ephemeral=True)
    if not spreadsheet:
        await ctx.followup.send("スプレッドシートに接続できていません。", ephemeral=True); return
    try:
        all_items = worksheet.get_all_records()
        embed = create_checklist_embed(all_items)
        await ctx.followup.send(embed=embed)
    except Exception as e:
        await ctx.followup.send(f"リスト表示中にエラーが発生: {e}", ephemeral=True)

@bot.slash_command(description="複数のキャラクターのレベルを一度に登録・更新します。", guild_ids=GUILD_IDS)
async def bulk_update(ctx):
    await ctx.defer(ephemeral=True)
    if not CATEGORIES:
        await ctx.followup.send("キャラクターリストが読み込めていません。", ephemeral=True)
        return
    
    # The view is now simpler to call
    await ctx.followup.send("更新したいキャラクターのグループを選択してください。", view=GroupSelectionView())

@bot.slash_command(description="自分が登録した内容をスプレッドシートから表示します。", guild_ids=GUILD_IDS)
async def my_list(ctx):
    await ctx.defer(ephemeral=True)
    if not spreadsheet:
        await ctx.followup.send("スプレッドシートに接続できていません。", ephemeral=True); return
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
        await ctx.followup.send(embed=embed)
    except Exception as e:
        await ctx.followup.send(f"リスト表示中にエラーが発生: {e}", ephemeral=True)

@bot.slash_command(description="指定したキャラクターの所持者とレベルの一覧を表示します。", guild_ids=GUILD_IDS)
async def search(ctx, キャラクター名: discord.Option(str, "検索したいキャラクターの名前を入力してください")):
    await ctx.defer()
    if not spreadsheet:
        await ctx.followup.send("スプレッドシートに接続できていません。", ephemeral=True); return
    try:
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
    except Exception as e:
        await ctx.followup.send(f"検索中にエラーが発生: {e}", ephemeral=True)

@bot.slash_command(description="指定したキャラクターの所持状況やレベルを集計・分析します。", guild_ids=GUILD_IDS)
async def summary(
    ctx,
    キャラクター名: discord.Option(str, "集計したいキャラクターの名前を入力してください")
):
    await ctx.defer(ephemeral=True)
    
    if not spreadsheet:
        await ctx.followup.send("スプレッドシートに接続できていません。", ephemeral=True)
        return
        
    try:
        # スプレッドシートから全データを取得
        all_data = worksheet.get_all_records()

        # 入力されたキャラクター名でデータを絞り込む
        filtered_items = [row for row in all_data if row.get('キャラクター名') == キャラクター名]
        
        embed = discord.Embed(
            title=f"📊 「{キャラクター名}」の集計結果",
            color=discord.Color.gold()
        )

        if not filtered_items:
            embed.description = "このキャラクターを登録している人はいません。"
        else:
            # --- ↓↓ ここからが集計処理 ↓↓ ---
            
            # 1. 所持者数を計算
            owner_count = len(filtered_items)
            
            # 2. 最高レベルとその所持者を探す
            max_level = 0
            max_level_holder = ""
            total_level = 0
            
            for item in filtered_items:
                try:
                    # レベルを数値に変換（変換できない場合は無視）
                    level = int(item.get('レベル', 0))
                    total_level += level
                    if level > max_level:
                        max_level = level
                        max_level_holder = item.get('追加者', '不明')
                except (ValueError, TypeError):
                    continue # レベルが数値でないデータは無視
            
            # 3. 平均レベルを計算
            average_level = total_level / owner_count if owner_count > 0 else 0
            
            # --- ↑↑ 集計処理ここまで ↑↑ ---
            
            # 結果をEmbedに追加
            embed.add_field(name="所持者数", value=f"{owner_count} 人", inline=False)
            embed.add_field(name="最高レベル", value=f"Lv. {max_level} (所持者: {max_level_holder})", inline=False)
            embed.add_field(name="平均レベル", value=f"約 Lv. {average_level:.1f}", inline=False) # 小数点以下1桁まで表示
        
        await ctx.followup.send(embed=embed)

    except Exception as e:
        await ctx.followup.send(f"集計中にエラーが発生しました: {e}", ephemeral=True)

@bot.slash_command(description="指定したキャラクターの評価情報を表示します。", guild_ids=GUILD_IDS)
async def character_info(
    ctx,
    キャラクター名: discord.Option(str, "評価を知りたいキャラクターの名前") # choicesを削除
):
    await ctx.defer()
    if not info_worksheet:
        await ctx.followup.send("キャラクター一覧シートに接続できていません。", ephemeral=True); return
    try:
        all_char_data = info_worksheet.get_all_records()
        char_data = None
        for row in all_char_data:
            if row.get("キャラクター名") == キャラクター名:
                char_data = row; break
        if not char_data:
            await ctx.followup.send("そのキャラクターの情報は見つかりませんでした。"); return
            
        embed = discord.Embed(title=f"📝 「{キャラクター名}」のキャラクター情報", description=char_data.get("評価内容", "評価内容は未記載です。"), color=discord.Color.teal())
        embed.add_field(name="育成優先度", value=f"**{char_data.get('育成優先度', 'N/A')}**", inline=True)
        embed.add_field(name="スタンス開放優先度", value=f"**{char_data.get('スタンス開放優先度', 'N/A')}**", inline=True)
        embed.add_field(name="英雄召喚優先度", value=f"**{char_data.get('英雄召喚チケット優先度', 'N/A')}**", inline=True)
        stances = f"・{char_data.get('スタンス1', '---')}\n・{char_data.get('スタンス2', '---')}"
        embed.add_field(name="習得スタンス", value=stances, inline=False)
        await ctx.followup.send(embed=embed)
    except Exception as e:
        await ctx.followup.send(f"情報取得中にエラーが発生しました: {e}", ephemeral=True)

@bot.slash_command(description="次のコインブラFBの時間を通知します。", guild_ids=GUILD_IDS)
async def coinbra_fb(ctx):
    next_fb_time = calculate_next_fb("2025/08/25 04:00", 10)
    await ctx.respond(f"次のコインブラFBは **{next_fb_time.strftime('%m月%d日 %H時')}** です。", ephemeral=True)

@bot.slash_command(description="次のオーシュFBの時間を通知します。", guild_ids=GUILD_IDS)
async def oshu_fb(ctx):
    next_fb_time = calculate_next_fb("2025/08/25 10:00", 21)
    await ctx.respond(f"次のオーシュFBは **{next_fb_time.strftime('%m月%d日 %H時')}** です。", ephemeral=True)
    
@bot.slash_command(description="ダイスを振り、0から100までの数字をランダムに選びます。", guild_ids=GUILD_IDS)
async def diceroll(ctx):
    # 0から100までの整数をランダムに選ぶ
    result = random.randint(0, 100)
    await ctx.respond(f"🎲 ダイスの結果は **{result}** でした！")

@bot.slash_command(description="指定した都道府県の今日の天気を表示します。", guild_ids=GUILD_IDS)
async def weather(
    ctx,
    # ↓↓↓ choices=... の部分を削除しました ↓↓↓
    都道府県: discord.Option(str, "天気を知りたい都道府県名を入力してください")
):
    await ctx.defer()
    
    code = PREFECTURE_CODES.get(都道府県)
    if not code:
        # 「県」や「都」などを除いた名前でも検索できるようにする
        for key in PREFECTURE_CODES.keys():
            if 都道府県 in key:
                code = PREFECTURE_CODES[key]
                break

    if not code:
        await ctx.followup.send(f"「{都道府県}」が見つかりませんでした。都道府県名を正しく入力してください。", ephemeral=True)
        return
        
    try:
        # 気象庁の天気予報APIにリクエストを送信
        url = f"https://www.jma.go.jp/bosai/forecast/data/forecast/{code}.json"
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        
        publishing_office = data[0]['publishingOffice']
        report_datetime_str = data[0]['reportDatetime']
        area_name = data[0]['timeSeries'][0]['areas'][0]['area']['name']
        weather_today = data[0]['timeSeries'][0]['areas'][0]['weathers'][0]
        
        temp_data = None
        for series in data[0]['timeSeries']:
            if 'temps' in series['areas'][0]:
                temp_data = series['areas'][0]['temps']
                break
        
        temp_info = "（気温情報なし）"
        if temp_data and len(temp_data) >= 2:
            min_temp = temp_data[0]
            max_temp = temp_data[1]
            temp_info = f"🌡️ 最低: {min_temp}°C / 最高: {max_temp}°C"
        
        report_datetime = datetime.datetime.fromisoformat(report_datetime_str)
        report_time_formatted = report_datetime.strftime('%Y年%m月%d日 %H:%M')
        
        embed = discord.Embed(
            title=f"🗾 {area_name}の天気予報",
            description=f"**{weather_today}**\n{temp_info}",
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"{publishing_office}発表 | {report_time_formatted}")
        
        await ctx.followup.send(embed=embed)

    except Exception as e:
        await ctx.followup.send(f"天気情報の取得中にエラーが発生しました: {e}", ephemeral=True)
        
@bot.event
async def on_application_command_error(ctx: discord.ApplicationContext, error: discord.DiscordException):
    response_message = "コマンド実行中に予期せぬエラーが発生しました。管理者にご確認ください。"
    if isinstance(error, WrongChannelError):
        response_message = "このコマンドは指定されたチャンネルでのみ使用できます。"
    
    # defer済みかどうかに関わらず、応答を試みる
    try:
        if ctx.interaction.response.is_done():
            await ctx.followup.send(response_message, ephemeral=True)
        else:
            await ctx.respond(response_message, ephemeral=True)
    except discord.errors.NotFound: # interactionがタイムアウトしている場合など
        pass # エラーメッセージの送信に失敗しても何もしない
    
    print(f"コマンド {ctx.command.name} でエラーが発生: {error}")

# .env読み込みとBot起動
bot.run(os.getenv("DISCORD_TOKEN"))




















