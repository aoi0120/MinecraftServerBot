import discord
import subprocess
import asyncio
import os
import glob
import logging
import time
import signal
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv

# バージョン情報
VERSION = "1.0.1"

# ログ設定
logger = logging.getLogger("discord_bot")
logger.setLevel(logging.DEBUG)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter("[%(asctime)s] - [%(levelname)s] - %(name)s - %(message)s", "%Y-%m-%d %H:%M:%S")
console_handler.setFormatter(console_formatter)
logger.addHandler(console_handler)

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
    "fail_count": 0,
    "launching": False
}

last_checked = time.time()

TARGET_CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
FORGE_SCRIPT_PATH = os.getenv("JAVA_SCRIPT_PATH")
FORGE_PATH = os.getenv("JAVA_PATH")


def is_server_running() -> bool:
    proc = state.get("server_process")
    if proc and proc.poll() is None:
        logger.debug("state['server_process'] による確認: 実行中")
        return True
    logger.debug("state['server_process'] による確認: 停止中")
    return False

def launch_forge_process() -> subprocess.Popen:
    logger.info("サーバーを直接 java コマンドで起動します。")
    with open(state["log_file_path"], "a") as log_file:
        proc = subprocess.Popen(
            [
                "java",
                "-Xmx8G", "-Xms4G",
                "@user_jvm_args.txt",
                "@libraries/net/minecraftforge/forge/1.20.1-47.3.0/unix_args.txt",
                "nogui"
            ],
            stdout=log_file,
            stderr=log_file,
            stdin=subprocess.PIPE,
            preexec_fn=os.setsid,
            cwd=FORGE_PATH,
        )
        logger.debug(f"サーバープロセスID: {proc.pid}")
        return proc

def stop_server(force=False) -> bool:
    proc = state.get("server_process")

    if proc and proc.poll() is None:
        try:
            if force:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                logger.info("強制的にサーバーを停止します。")
            else:
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

async def wait_for_server_startup(log_path, timeout=120):
    import time
    import os

    logger.info("サーバーの完全起動を待機中...")
    start_time = time.time()
    last_size = 0

    await asyncio.sleep(5)

    while time.time() - start_time < timeout:
        if not os.path.exists(log_path):
            logger.debug("ログファイルがまだ存在しません。")
            await asyncio.sleep(1)
            continue

        try:
            current_size = os.path.getsize(log_path)

            if current_size == last_size:
                await asyncio.sleep(1)
                continue
            last_size = current_size

            with open(log_path, "rb") as f:
                f.seek(max(current_size - 20000, 0), os.SEEK_SET)
                content = f.read().decode("utf-8", errors="ignore")

                if (
                    "Done (" in content
                    or 'For help, type "help"' in content
                    or "All dimensions are saved" in content
                ):
                    logger.info("サーバーが正常に起動しました。")
                    return True

        except Exception as e:
            logger.error(f"ログファイルの読み込み中にエラーが発生しました: {e}")

        await asyncio.sleep(2)

    logger.error("サーバーの起動を待機中にタイムアウトしました。")
    return False


async def start_forge_server(channel):
    if state["launching"]:
        await channel.send("サーバーはすでに起動中です。しばらくお待ちください。")
        logger.warning("サーバーはすでに起動処理中です。")
        return

    if is_server_running():
        await channel.send("サーバーは既に起動しています。")
        logger.warning("要求されたが、サーバーは既に実行中です。")
        return

    state["launching"] = True
    try:
        proc = launch_forge_process()
        state["server_process"] = proc
        logger.info("サーバーが起動しました。")

        log_path = os.path.join(os.getenv("LOG_DIR"), "latest.log")
        ready = await wait_for_server_startup(log_path)

        if ready:
            await channel.send("サーバーが正常に起動しました。")
        else:
            await channel.send("サーバーの起動に失敗しました。")
    except Exception as e:
        await channel.send(f"サーバーの起動中にエラーが発生しました: {e}")
        logger.exception("サーバーの起動中にエラーが発生しました。")
    finally:
        state["launching"] = False

async def monitor_server():
    await client.wait_until_ready()
    channel = client.get_channel(TARGET_CHANNEL_ID)
    if channel is None:
        logger.error("チャンネルが見つかりません。IDが正しいか確認してください。")
        return

    global last_checked

    while not client.is_closed():
        await asyncio.sleep(30)

        crash_dir = os.getenv("CRASH_DIR")
        if crash_dir and os.path.exists(crash_dir):
            crash_files = glob.glob(os.path.join(crash_dir, "crash-*-server.txt"))
            new_crashes = [f for f in crash_files if os.path.getctime(f) > last_checked]
            if new_crashes:
                last_checked = time.time()
                latest_file = max(new_crashes, key=os.path.getctime)
                await channel.send(content="新しいクラッシュレポートが検出されました。", file=discord.File(latest_file))
                logger.info(f"新しいクラッシュレポートを送信しました: {latest_file}")

        if is_server_running():
            state["fail_count"] = 0
            logger.info("サーバーは実行中です。")
            continue

        if state["launching"]:
            logger.warning("起動処理中のため、再起動をスキップします。")
            continue

        state["fail_count"] += 1

        if state["fail_count"] >= 3:
            await channel.send("サーバーが起動していません。")
            logger.warning("サーバーが停止していることを検出しました。")
            if stop_server(force=True):
                logger.info("サーバーを再起動します。")
                await asyncio.sleep(10)
                await channel.send("再起動を試みます。")
                await start_forge_server(channel)
                state["fail_count"] = 0


@client.event
async def on_ready():
    logger.info(f"Botにログインしました: {client.user}")

@client.event
async def on_message(message):
    if message.author.bot:
        return

    content = message.content.strip().lower()

    if content == "/start":
        if state["monitor_task"] is None or state["monitor_task"].done():
            state["monitor_task"] = client.loop.create_task(monitor_server())
            logger.info("監視ループを再開しました。")
        await message.channel.send("リクエストを受け付けました。サーバーを起動します...")
        await start_forge_server(message.channel)

    elif content == "/stop":
        if stop_server():
            await message.channel.send("サーバーを停止しました。")
            if state["monitor_task"]:
                state["monitor_task"].cancel()
                logger.info("監視ループを停止しました。")
        else:
            await message.channel.send("サーバーは実行されていません。")

    elif content == "/restart":
        if stop_server():
            await message.channel.send("サーバーを停止しました。再起動します...")
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

    elif content == "/status":
        running = is_server_running()
        status_message = "サーバーは実行中です。" if running else "サーバーは停止しています。"
        await message.channel.send(status_message)

    elif content == "/log":
        log_dir = os.getenv("LOG_DIR")
        log_file_path = os.path.join(log_dir, "latest.log")
        if os.path.exists(log_file_path):
            await message.channel.send(file=discord.File(log_file_path))
        else:
            await message.channel.send("ログファイルが見つかりません。")

    elif content == "/crash-log":
        crash_dir = os.getenv("CRASH_DIR")
        crash_files = glob.glob(os.path.join(crash_dir, "crash-*-server.txt"))
        if crash_files:
            latest = max(crash_files, key=os.path.getctime)
            await message.channel.send(file=discord.File(latest))
        else:
            await message.channel.send("クラッシュレポートが見つかりません。")

    elif content == "/version":
        await message.channel.send(f"Botバージョン: {VERSION}")

    elif content == "/help":
        help_text = (
            "**コマンド一覧**\n"
            "/start - サーバーを起動します。\n"
            "/stop - サーバーを停止します。\n"
            "/restart - サーバーを再起動します。\n"
            "/status - サーバーの状態を確認します。\n"
            "/log - 最新のログファイルを送信します。\n"
            "/crash-log - 最新のクラッシュレポートを送信します。\n"
            "/version - このBotのバージョンを表示します。\n"
            "/help - このヘルプメッセージを表示します。"
        )
        await message.channel.send(help_text)


client.run(TOKEN)
