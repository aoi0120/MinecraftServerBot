import discord
import subprocess
import asyncio
import os
import glob
import logging
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv

#ログ設定
logger = logging.getLogger("discord_bot")
logger.setLevel(logging.DEBUG)

# コンソールハンドラの設定
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter("[%(asctime)s] - [%(levelname)s] - %(name)s - %(message)s", "%Y-%m-%d %H:%M:%S")
console_handler.setFormatter(console_formatter)
logger.addHandler(console_handler)

# ファイルハンドラの設定
file_handler = RotatingFileHandler("bot_debug.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8")
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(console_formatter)
logger.addHandler(file_handler)

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

state = {
    "server_process": None,
    "log_file_path": "server.log",
    "monitor_task": None,
}

# 通知を送りたいチャンネルIDをここに設定（整数）
TARGET_CHANNEL_ID = int(os.getenv("CHANNEL_ID"))  # ここに通知したいチャンネルIDを入力
FORGE_SCRIPT_PATH = os.getenv("JAVA_SCRIPT_PATH")
FORGE_PATH = os.getenv("JAVA_PATH")

# これはLinuxのpgrepコマンドを使用して、特定のプロセスが実行中かどうかを確認します。
def is_server_running() -> bool:
    result = subprocess.run(["pgrep", "-f", "forge.*nogui"], capture_output=True, text=True)
    running = bool(result.stdout.strip())
    logger.debug(f"サーバー実行状態: {'実行中' if running else '停止中'}")
    return running


# サーバーが実行中でない場合は、サーバーを起動します。
def stop_server() -> bool:
    proc = state.get("server_process")

    if proc and proc.poll() is None:
        try:
            proc.stdin.write(b"stop\n")
            proc.stdin.flush()
            proc.wait(timeout=60)
            logger.info("サーバーを正常に停止しました。")
            state["server_process"] = None
            return True
        except Exception as e:
            logger.error(f"サーバーの停止中にエラーが発生しました: {e}")
            return False
    else:
        logger.info("サーバーは実行されていません。")
        return False

# subprocess.Popenを使用して、非同期にサーバーを起動します。
def launch_forge_process() -> subprocess.Popen:
    logger.info("サーバーを起動します。")

    with open(state["log_file_path"], "a") as log_file:
        proc = subprocess.Popen(
            ["/bin/bash", FORGE_SCRIPT_PATH],
            stdout=log_file,
            stderr=log_file,
            stdin=subprocess.PIPE,
            preexec_fn=os.setsid,
            cwd=FORGE_PATH,
        )
        logger.debug(f"サーバープロセスID: {proc.pid}")
        return proc


async def start_forge_server(channel):
    if is_server_running():
        await channel.send("サーバーは既に起動しています。")
        logger.warning("要求されたが、サーバーは既に実行中です。")
        return

### サーバーを起動する
    try:
        proc = launch_forge_process()
        state["server_process"] = proc
        logger.info("サーバーが起動しました。")

        log_path = os.path.join(os.getenv("LOG_DIR"), "latest.log")
        ready = await wait_for_server_startup(log_path)

        if ready:
            await channel.send("サーバーが正常に起動しました。")
            logger.info("サーバーが正常に起動しました。")
        else:
            await channel.send("サーバーの起動に失敗しました。")
            logger.error("サーバーの起動に失敗しました。")

    except Exception as e:
        await channel.send(f"サーバーの起動中にエラーが発生しました: {e}")
        logger.exception("サーバーの起動中にエラーが発生しました。")


# javaのログファイルを送信する関数
async def send_log_file(channel):
    log_dir = os.getenv("LOG_DIR")
    log_file_path = os.path.join(log_dir, "latest.log")

    if not os.path.exists(log_file_path):
        await channel.send("ログファイルが見つかりません。")
        logger.error("ログファイルが見つかりません。")
        return

    await channel.send(content="最新のログファイルです。", file=discord.File(log_file_path))
    logger.info("最新のログファイルを送信しました。")


# javaのクラッシュレポートを送信する関数
async def send_crash_report(channel):
    crash_dir = os.getenv("CRASH_DIR")

    if not os.path.exists(crash_dir):
        await channel.send("クラッシュレポートディレクトリが見つかりません。")
        logger.error("クラッシュレポートディレクトリが見つかりません。")
        return

    crash_files = glob.glob(os.path.join(crash_dir, "crash-*-server.txt"))

    if not crash_files:
        await channel.send("クラッシュレポートが見つかりません。")
        logger.info("クラッシュレポートが見つかりません。")
        return

    latest_file = max(crash_files, key=os.path.getctime)
    await channel.send(content="最新のクラッシュレポートです。", file=discord.File(latest_file))

#Logファイルを監視して、サーバーが起動するのを待つ関数
async def wait_for_server_startup(log_path, timeout=120):
    import time

    logger.info("サーバーの完全起動を待機中...")
    start_time = time.time()

    while time.time() - start_time < timeout:
        if not os.path.exists(log_path):
            await asyncio.sleep(1)
            continue

        try:
            with open(log_path, "rb") as f:
                try:
                    f.seek(-3000, os.SEEK_END)
                except OSError:
                    f.seek(0)

                content = f.read().decode("utf-8", errors="ignore")

                if "Done (" in content:
                    logger.info("サーバーが正常に起動しました。")
                    return True
        except Exception as e:
            logger.error(f"ログファイルの読み込み中にエラーが発生しました: {e}")

        await asyncio.sleep(2)

    logger.error("サーバーの起動を待機中にタイムアウトしました。")
    return False



@client.event
async def on_ready():
    logger.info(f"Botにログインしました: {client.user}")

async def monitor_server():
    await client.wait_until_ready()
    channel = client.get_channel(TARGET_CHANNEL_ID)
    if channel is None:
        logger.error("チャンネルが見つかりません。IDが正しいか確認してください。")
        return

    fail_count = 0
    # サーバーの状態を監視するループ
    while not client.is_closed():
        await asyncio.sleep(30)

        if is_server_running():
            fail_count = 0
            continue

        fail_count += 1

        if not is_server_running():
            proc = state.get("server_process")
            if proc and proc.poll() is None:
                try:
                    proc.wait(timeout=10)
                    logger.info("サーバーが正常に停止しました。")
                except Exception as e:
                    logger.error(f"サーバーの停止中にエラーが発生しました: {e}")

        if fail_count >= 3:
            await channel.send("サーバーが起動していません。")
            logger.warning("サーバーが停止してることを検出しました。")

            # 再起動を試みる
            logger.info("サーバーを再起動します。")
            await start_forge_server(channel)

@client.event
async def on_message(message):

    if message.author.bot:
        return

    elif message.content == "/start":

        if state["monitor_task"] is None or state["monitor_task"].done():
            state["monitor_task"] = client.loop.create_task(monitor_server())
            logger.info("監視ループを再開しました。")

        await message.channel.send("リクエストを受け付けました。サーバーを起動します...")
        logger.info("/startコマンドでサーバー起動リクエストを受けました。")
        await start_forge_server(message.channel)


    elif message.content == "/stop":

        logger.info("/stopコマンドでサーバーを停止しました。")

        if stop_server():
            await message.channel.send("サーバーを停止しました。")

            if state["monitor_task"]:
                state["monitor_task"].cancel()
                logger.info("監視ループを停止しました。")
        else:
            await message.channel.send("サーバーは実行されていません。")

    elif message.content == "/restart":

        logger.info("/restartコマンドでサーバーを再起動しました。")

        if stop_server():
            await message.channel.send("サーバーを停止しました。")
            if state["monitor_task"]:
                state["monitor_task"].cancel()
                try:
                    await state["monitor_task"]
                except asyncio.CancelledError:
                    logger.info("監視ループを停止しました。")


            await asyncio.sleep(20)
            await start_forge_server(message.channel)
            state["monitor_task"] = client.loop.create_task(monitor_server())
        else:
            await message.channel.send("サーバーは実行されていません。")

    elif message.content == "/log":
        logger.info("/logコマンドでログファイルを送信します。")
        try:
            await send_log_file(message.channel)
        except Exception as e:
            await message.channel.send(f"ログ送信エラー: {e}")

    elif message.content == "/crash-log":
        logger.info("/crash-logコマンドでログファイルを送信します。")
        try:
            await send_crash_report(message.channel)
        except Exception as e:
            await message.channel.send(f"ログ送信エラー: {e}")

    elif message.content == "/help":
        help_text = (
            "**コマンド一覧**\n"
            "/start - サーバーを起動します。\n"
            "/stop - サーバーを停止します。\n"
            "/restart - サーバーを再起動します。\n"
            "/log - 最新のログファイルを送信します。\n"
            "/crash-log - 最新のクラッシュレポートを送信します。\n"
            "/help - このヘルプメッセージを表示します。\n"
        )
        await message.channel.send(help_text)
        logger.info("/helpコマンドでヘルプメッセージを送信しました。")


client.run(TOKEN)
