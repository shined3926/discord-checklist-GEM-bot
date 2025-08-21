import discord
from discord.ui import View, Modal, InputText, Select, Button
import json
import gspread
import os
from dotenv import load_dotenv

# --- 設定項目 ---
load_dotenv()
GUILD_IDS = [int(id_str) for id_str in os.getenv("GUILD_IDS", "").split(',') if id_str]
DATA_FILE = "data.json"
CREDENTIALS_FILE = "credentials.json"
SPREADSHEET_NAME = "グラナドエスパダM 党員所持リスト"
# ----------------

# --- Googleスプレッドシート連携 ---
spreadsheet = None
worksheet = None
CATEGORIES = []
try:
    gc = gspread.service_account(filename=CREDENTIALS_FILE)
    spreadsheet = gc.open(SPREADSHEET_NAME)
    worksheet = spreadsheet.worksheet("BOT書き込み用")
    print("スプレッドシート「BOT書き込み用」への接続に成功しました。")
    # キャラクターリストのシートを読み込む
    char_worksheet = spreadsheet.worksheet("キャラクターリスト")
    # A列の値をすべて取得 (ヘッダーを除く)
    all_names = char_worksheet.col_values(1)
    if len(all_names) > 1:
        CATEGORIES = all_names[1:] # 2行目以降をカテゴリとして設定
    print(f"{len(CATEGORIES)} 件のキャラクターをスプレッドシートから読み込みました。")
except gspread.WorksheetNotFound as e:
    print(f"エラー: シートが見つかりません - {e}")
    spreadsheet = None
except Exception as e:
    print(f"スプレッドシートへの接続・読み込み中にエラーが発生しました: {e}")
    spreadsheet = None
# --------------------------------

# --- データ保存・読み込み機能 ---
def save_data(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def load_data():
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

# --- スプレッドシート更新機能 ---
def update_spreadsheet(all_checklists_data):
    if not spreadsheet: return
    header = ["キャラクター名", "レベル", "追加者"]
    rows = [header]
    for message_id, items in all_checklists_data.items():
        sorted_items = sorted(items, key=lambda x: x.get('category', ''))
        for item in sorted_items:
            rows.append([item.get('category', ''), item.get('value', ''), item.get('author', '')])
    worksheet.clear()
    worksheet.update(range_name='A1', values=rows)
    print("スプレッドシートを更新しました。")

# --- Discord Bot 本体 ---
MODAL_GROUP_SIZE = 5

bot = discord.Bot()
checklists = load_data()

def create_checklist_embed(items):
    embed = discord.Embed(title="共有チェックリスト", color=discord.Color.blue())
    if not items:
        embed.description = "下のメニューからキャラクターを選んでレベルを追加してください。"
    else:
        description = ""
        sorted_items = sorted(items, key=lambda x: x.get('category', ''))
        for item in sorted_items:
            author_info = f" (追加者: {item['author']})" if 'author' in item and item['author'] else ""
            description += f"{item['category']}: Lv. {item['value']}{author_info}\n"
        embed.description = description
    return embed

class AddItemModal(Modal):
    def __init__(self, category: str, original_message):
        super().__init__(title=f"{category} のレベル入力")
        self.category, self.original_message = category, original_message
        self.add_item(InputText(label="レベル", placeholder="例：90"))

    async def callback(self, interaction: discord.Interaction):
        message_id = str(self.original_message.id)
        if message_id not in checklists:
            await interaction.response.send_message("このリストは古いです。", ephemeral=True); return
        item_value, author_name = self.children[0].value, interaction.user.display_name
        item_found = False
        for item in checklists[message_id]:
            if item.get('category') == self.category and item.get('author') == author_name:
                item['value'] = item_value; item_found = True; break
        if not item_found:
            checklists[message_id].append({"category": self.category, "value": item_value, "author": author_name})
        save_data(checklists); update_spreadsheet(checklists)
        await self.original_message.edit(embed=create_checklist_embed(checklists[message_id]))
        response_message = "のレベルを更新しました！" if item_found else "をリストに追加しました！"
        await interaction.response.send_message(f"`{self.category}` {response_message}", ephemeral=True)

class BulkUpdateModal(Modal):
    def __init__(self, characters_to_update: list, original_message, author_name: str):
        super().__init__(title="キャラクターレベルの一括更新")
        self.characters, self.original_message, self.author_name = characters_to_update, original_message, author_name
        user_items = {}
        # スプレッドシートに接続できている場合のみ、データを読み込む
        if spreadsheet:
            try:
                all_data = worksheet.get_all_records()
                # スプレッドシートのデータから、自分の登録データだけを抽出
                user_items = {
                    row['キャラクター名']: row['レベル'] 
                    for row in all_data 
                    if row.get('追加者') == self.author_name
                }
            except Exception as e:
                print(f"一括更新のためのデータ読み込み中にエラー: {e}")
        # --- ↑↑↑ 変更ここまで ↑↑↑ ---

        for char_name in self.characters:
            current_level = user_items.get(char_name, "") # 現在のレベルを取得
            self.add_item(InputText(
                label=char_name,
                placeholder=f"現在のレベル: {current_level}" if current_level else "未登録",
                custom_id=char_name,
                required=False
            ))

    async def callback(self, interaction: discord.Interaction):
        # (callbackの中身は変更ありません)
        message_id = str(self.original_message.id)
        if message_id not in checklists:
            await interaction.response.send_message("このリストは古いです。", ephemeral=True); return
        updated_chars = []
        for field in self.children:
            if field.value:
                char_name, new_level = field.custom_id, field.value
                updated_chars.append(char_name)
                item_found = False
                for item in checklists[message_id]:
                    if item.get('category') == char_name and item.get('author') == self.author_name:
                        item['value'] = new_level; item_found = True; break
                if not item_found:
                    checklists[message_id].append({"category": char_name, "value": new_level, "author": self.author_name})
        if updated_chars:
            save_data(checklists); update_spreadsheet(checklists)
            await self.original_message.edit(embed=create_checklist_embed(checklists[message_id]))
            await interaction.response.send_message(f"{', '.join(updated_chars)} のレベルを更新しました。", ephemeral=True)
        else:
            await interaction.response.send_message("更新するレベルが入力されませんでした。", ephemeral=True)

class GroupSelectionView(View):
    def __init__(self, original_message):
        super().__init__(timeout=180)
        self.original_message = original_message
        
        category_chunks = [CATEGORIES[i:i + MODAL_GROUP_SIZE] for i in range(0, len(CATEGORIES), MODAL_GROUP_SIZE)]
        # 5つのコンポーネント上限を超えないように、最初の5グループのみボタンを作成
        for i, chunk in enumerate(category_chunks[:5]):
            self.add_item(Button(
                label=f"グループ {i+1} ({chunk[0]}～)",
                style=discord.ButtonStyle.secondary,
                custom_id=f"group_select_{i}"
            ))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        custom_id = interaction.data.get("custom_id")
        if custom_id and custom_id.startswith("group_select"):
            group_index = int(custom_id.split('_')[-1])
            category_chunks = [CATEGORIES[i:i + MODAL_GROUP_SIZE] for i in range(0, len(CATEGORIES), MODAL_GROUP_SIZE)]
            selected_chunk = category_chunks[group_index]
            modal = BulkUpdateModal(characters_to_update=selected_chunk, original_message=self.original_message, author_name=interaction.user.display_name)
            await interaction.response.send_modal(modal); return False
        return True

class ChecklistView(View):
    def __init__(self):
        super().__init__(timeout=None)
        # 1つのドロップダウンの選択肢上限は25
        chunk_size = 25
        category_chunks = [CATEGORIES[i:i + chunk_size] for i in range(0, len(CATEGORIES), chunk_size)]
        # 5つのコンポーネント上限を超えないように、最初の5つのドロップダウンのみ作成
        for i, chunk in enumerate(category_chunks[:5]):
            options = [discord.SelectOption(label=cat) for cat in chunk]
            self.add_item(Select(placeholder=f"キャラ選択 ({i*chunk_size+1}～)...", options=options, custom_id=f"category_select_{i}"))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        custom_id = interaction.data.get("custom_id")
        if custom_id and custom_id.startswith("category_select"):
            selected_category = interaction.data["values"][0]
            modal = AddItemModal(category=selected_category, original_message=interaction.message)
            await interaction.response.send_modal(modal); return False
        return True

@bot.event
async def on_ready():
    print(f"{bot.user}としてログインしました")
    bot.add_view(ChecklistView())
    bot.add_view(GroupSelectionView())

@bot.slash_command(description="共有チェックリストを作成します。", guild_ids=GUILD_IDS)
async def checklist(ctx):
    response = await ctx.respond(embed=create_checklist_embed([]), view=ChecklistView())
    message = await response.original_response()
    message_id = str(message.id)
    if message_id not in checklists:
        checklists[message_id] = []; save_data(checklists); update_spreadsheet(checklists)
    if spreadsheet:
        await ctx.followup.send(f"リストを作成しました。\nスプレッドシート:\n{spreadsheet.url}", ephemeral=True)

@bot.slash_command(description="複数のキャラクターのレベルを一度に更新します。", guild_ids=GUILD_IDS)
async def bulk_update(ctx):
    target_message = None
    async for message in ctx.channel.history(limit=100):
        # "共有チェックリスト" のタイポを修正
        if message.author == bot.user and message.embeds and message.embeds[0].title == "共有チェックリスト":
            target_message = message; break
    if not target_message:
        await ctx.respond("更新対象の共有チェックリストが見つかりません。先に`/checklist`コマンドを実行してください。", ephemeral=True); return
    view = GroupSelectionView(original_message=target_message)
    await ctx.respond("更新したいキャラクターのグループを選択してください。", view=view, ephemeral=True)

@bot.slash_command(description="自分が登録したキャラクターとレベルの一覧をスプレッドシートから表示します。", guild_ids=GUILD_IDS)
async def my_list(ctx):
    # スプレッドシートに接続できているか確認
    if not spreadsheet:
        await ctx.respond("スプレッドシートに接続できていません。管理者にご連絡ください。", ephemeral=True)
        return

    try:
        # スプレッドシートから全データを取得（ヘッダーをキーにした辞書のリストとして）
        all_data = worksheet.get_all_records()
    except Exception as e:
        print(f"スプレッドシートの読み込み中にエラーが発生しました: {e}")
        await ctx.respond("スプレッドシートのデータを読み込めませんでした。", ephemeral=True)
        return
    
    author_name = ctx.author.display_name

    # 自分が登録したデータだけを抽出
    # スプレッドシートの '追加者' 列と自分の名前を比較
    my_items = [row for row in all_data if row.get('追加者') == author_name]

    embed = discord.Embed(title=f"{author_name}さんの登録キャラクター一覧（スプレッドシート準拠）", color=discord.Color.green())

    if not my_items:
        embed.description = "あなたが登録したキャラクターは見つかりませんでした。"
    else:
        description = ""
        # スプレッドシートの 'キャラクター名' 列でソート
        sorted_items = sorted(my_items, key=lambda x: x.get('キャラクター名', ''))
        for item in sorted_items:
            # スプレッドシートの列名（'キャラクター名', 'レベル'）を使って表示
            description += f"{item['キャラクター名']}: Lv. {item['レベル']}\n"
        embed.description = description
    
    await ctx.respond(embed=embed, ephemeral=True) # 自分にだけ見えるメッセージで返信

# .envファイルからトークンを読み込んでBotを起動
load_dotenv()

bot.run(os.getenv("DISCORD_TOKEN"))
