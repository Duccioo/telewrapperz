#!/usr/bin/env python3
import asyncio
import os
import sys
from collections import deque
from datetime import datetime
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler

from telewrapperz.bot import TeleWrapperzBot
from telewrapperz.config import load_config
from telewrapperz.logs import LogBuffer
from telewrapperz.process import ProcessManager
from telewrapperz.system_stats import SystemMonitor


async def run_test_mode(token, chat_id):
    """Runs a quick diagnostic test of bot functionality."""
    print("🔵 Starting Telewrapperz functionality test...")
    try:
        app = Application.builder().token(token).build()
        monitor = SystemMonitor()

        async with app:
            await app.start()

            print("📨 Sending test message to Telegram...")
            msg_text = (
                "🔔 <b>Telewrapperz Test</b>\n\n"
                "If you see this message, the bot is working properly!\n"
                "Checking system statistics..."
            )
            await app.bot.send_message(
                chat_id=chat_id, text=msg_text, parse_mode=ParseMode.HTML
            )
            print("✅ Message sent.")

            print("📊 Checking system statistics...")
            metrics = monitor.get_metrics()
            cpu = metrics["cpu"]
            mem = metrics["memory"]
            cpu_temp = metrics["cpu_temp"]
            gpu = metrics["gpu_info"]
            disk_info = metrics["disk_info"]

            stats_msg = (
                f"✅ <b>Test Completed</b>\n\n"
                f"CPU: {cpu}%\n"
                f"RAM: {mem}%\n"
                f"{disk_info}\n"
                f"CPU Temp: {f'{cpu_temp:.0f}°C' if cpu_temp is not None else 'Not available'}\n"
                f"GPU: {gpu if gpu else 'Not detected/Available'}"
            )
            print(f"   CPU: {cpu}%, RAM: {mem}%, {disk_info}, CPU Temp: {cpu_temp}, GPU: {gpu}")

            await app.bot.send_message(
                chat_id=chat_id, text=stats_msg, parse_mode=ParseMode.HTML
            )
            print("✅ Statistics sent.")

            await app.stop()
            monitor.close()

        print("🟢 Test completed successfully!")

    except Exception as e:
        print(f"❌ ERROR during test: {e}")
        sys.exit(1)


async def main():
    (
        command,
        token,
        chat_id,
        update_interval,
        is_test,
        enable_log,
        enable_cpu_temperature_alert,
        queue_until,
        queue_check_interval,
        show_disk,
        notify_on_completion,
    ) = load_config()

    if not token or not chat_id:
        print("Error: Token and Chat ID are required (via CLI, Config, or ENV).")
        sys.exit(1)

    if is_test:
        await run_test_mode(token, chat_id)
        return

    if not command:
        print("Error: You must specify a command to execute (or use --test).")
        sys.exit(1)

    print(f"Starting Wrapper for: {command} (update interval: {update_interval}s)")

    log_file_path = None
    if enable_log:
        log_dir = "telewrapperz_log"
        os.makedirs(log_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file_path = os.path.join(log_dir, f"telewrapperz_{timestamp}.log")
        print(f"Logging output to: {log_file_path}")

    log_buffer = LogBuffer()
    system_monitor = SystemMonitor()
    process_manager = ProcessManager(
        command, os.getcwd(), log_buffer, log_file_path=log_file_path
    )
    process_task = None
    app = Application.builder().token(token).build()

    async def run_and_notify():
        await process_manager.run()
        await bot.notify_completion(app.bot)
        await bot.update_dashboard_message(app.bot, force=True)

    async def start_process():
        nonlocal process_task
        if process_task is None:
            process_task = asyncio.create_task(run_and_notify())

    bot = TeleWrapperzBot(
        token,
        chat_id,
        command,
        process_manager,
        system_monitor,
        update_interval,
        log_file_path=log_file_path,
        enable_cpu_temperature_alert=enable_cpu_temperature_alert,
        queue_until=queue_until,
        queue_check_interval=queue_check_interval,
        start_process=start_process,
        show_disk=show_disk,
        enable_completion_notification=notify_on_completion,
    )

    app.add_handler(CallbackQueryHandler(bot.handle_button))

    async with app:
        await app.start()
        await app.updater.start_polling()
        updater_task = asyncio.create_task(bot.telegram_updater(app))

        for _ in range(50):
            if bot.dashboard_message_id or updater_task.done():
                break
            await asyncio.sleep(0.1)

        if not queue_until:
            await start_process()
        await bot.update_dashboard_message(app.bot, force=True)

        while not bot.shutdown_signal:
            await asyncio.sleep(1)

        await app.updater.stop()
        updater_task.cancel()
        if process_task and not process_task.done():
            process_task.cancel()
        await app.stop()

    system_monitor.close()


def entry_point():
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted manually.")


if __name__ == "__main__":
    entry_point()
