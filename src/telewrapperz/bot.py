import asyncio
import html
import os
import socket
import uuid
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, NetworkError, RetryAfter, TimedOut
from telegram.ext import ContextTypes

from telewrapperz.logs import MAX_LOG_LINES, strip_ansi


class TeleWrapperzBot:
    CPU_TEMPERATURE_ALERT_THRESHOLD = 90

    def __init__(
        self,
        token,
        chat_id,
        command,
        process_manager,
        system_monitor,
        update_interval,
        log_file_path=None,
        enable_cpu_temperature_alert=True,
        queue_until=None,
        queue_check_interval=None,
        start_process=None,
        show_disk=False,
        enable_completion_notification=True,
    ):
        self.token = token
        self.chat_id = chat_id
        self.command = command
        self.process_manager = process_manager
        self.system_monitor = system_monitor
        self.update_interval = update_interval
        self.log_file_path = log_file_path
        self.enable_cpu_temperature_alert = enable_cpu_temperature_alert
        self.cpu_temperature_alert_sent = False
        self.enable_completion_notification = enable_completion_notification
        self.completion_notification_sent = False
        self.queue_until = queue_until
        self.queue_check_interval = queue_check_interval or update_interval
        self.start_process = start_process
        self.show_disk = show_disk
        self.queue_ready = not queue_until
        self.queue_notification_sent = False

        self.hostname = socket.gethostname()
        self.pid = os.getpid()
        self.session_id = str(uuid.uuid4())[:8]
        self.shutdown_signal = False
        self.start_time = datetime.now()

        self.dashboard_message_id = None
        self.last_message_text = None

    async def update_dashboard_message(self, bot_api, force=False):
        if not self.dashboard_message_id:
            return False

        text = self.build_dashboard_text()
        if not force and text == self.last_message_text:
            return False

        try:
            await bot_api.edit_message_text(
                chat_id=self.chat_id,
                message_id=self.dashboard_message_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=self.get_keyboard(),
            )
            self.last_message_text = text
            return True
        except BadRequest as e:
            if "Message is not modified" in str(e):
                return False
            print(f"Telegram BadRequest: {e}")
        except RetryAfter as e:
            print(f"Telegram FloodLimit: sleeping {e.retry_after}s")
            await asyncio.sleep(e.retry_after)
        except (NetworkError, TimedOut):
            pass
        except Exception as e:
            print(f"Telegram Update Error: {e}")

        return False

    def build_dashboard_text(self):
        """Constructs dashboard message text with live stats and logs."""
        cpu, mem, cpu_temp, gpu_stats = self.system_monitor.get_stats()
        now = datetime.now()
        duration = str(now - self.start_time).split(".")[0]
        now_time = now.strftime("%H:%M:%S")
        has_started = getattr(self.process_manager, "has_started", True)

        if not has_started:
            status_icon = (
                "⏳ Pending Approval"
                if self.queue_ready
                else "⏳ Queued"
            )
        elif self.process_manager.is_running:
            status_icon = "🟢 Running"
        elif self.process_manager.return_code == 0:
            status_icon = f"✅ Done (Exit: {self.process_manager.return_code})"
        else:
            status_icon = f"❌ Error (Exit: {self.process_manager.return_code})"

        raw_logs = self.process_manager.log_buffer.get_lines().rstrip("\n")
        clean_logs = strip_ansi(raw_logs)
        logs = html.escape(clean_logs)

        if not clean_logs.strip():
            logs = "Waiting to start..." if not has_started else "Starting..."

        safe_hostname = html.escape(self.hostname)
        safe_command = html.escape(self.command)
        safe_gpu_stats = html.escape(gpu_stats) if gpu_stats else ""

        header = (
            f"🖥 <b>{safe_hostname}</b> (PID: {self.pid})\n"
            f"⚙️ <code>{safe_command}</code>\n\n"
            f"Status: {status_icon}\n"
            f"Time: {duration}\n"
            f"Last Update: {now_time}\n"
            f"CPU: {cpu}% | RAM: {mem}%\n"
        )
        if self.show_disk:
            metrics = (
                self.system_monitor.get_metrics()
                if hasattr(self.system_monitor, "get_metrics")
                else {}
            )
            disk_info = metrics.get("disk_info")
            if disk_info:
                header += f"{html.escape(disk_info)}\n"
        if cpu_temp is not None:
            header += f"CPU Temp: {cpu_temp:.0f}°C\n"
        if safe_gpu_stats:
            header += f"<code>{safe_gpu_stats}</code>\n"

        header += f"\n📜 <b>Recent Log (Last {MAX_LOG_LINES}):</b>\n"
        if self.queue_until and not has_started:
            cond_status = (
                " (Verified ✅ - Pending approval)"
                if self.queue_ready
                else " (Waiting ⏳)"
            )
            header += f"⏳ Condition: <code>{html.escape(self.queue_until)}</code>{cond_status}\n"

        max_len = 4096
        overhead = len(header) + len("<pre></pre>") + 20
        available_chars = max_len - overhead

        if len(logs) > available_chars:
            trunc_msg = "\n...[truncated]...\n"
            keep_len = available_chars - len(trunc_msg)
            if keep_len > 0:
                logs = trunc_msg + logs[-keep_len:]
            else:
                logs = trunc_msg

        msg = f"{header}<pre>{logs}</pre>"
        return msg

    def get_keyboard(self):
        """Generates inline keyboard for dashboard."""
        pfx = f"{self.session_id}"

        buttons = [
            [InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh:{pfx}")],
        ]

        if self.log_file_path and os.path.exists(self.log_file_path):
            buttons.append(
                [
                    InlineKeyboardButton(
                        "📄 Download Log",
                        callback_data=f"download_log:{pfx}",
                    )
                ]
            )

        has_started = getattr(self.process_manager, "has_started", True)
        if not has_started:
            if self.queue_ready:
                buttons.append(
                    [
                        InlineKeyboardButton(
                            "🚀 Approve & Start", callback_data=f"start:{pfx}"
                        )
                    ]
                )
                buttons.append(
                    [
                        InlineKeyboardButton(
                            "❌ Close Wrapper", callback_data=f"exit:{pfx}"
                        )
                    ]
                )
            else:
                buttons.append(
                    [
                        InlineKeyboardButton(
                            "❌ Cancel Queue", callback_data=f"exit:{pfx}"
                        )
                    ]
                )
        elif self.process_manager.is_running:
            buttons.append(
                [
                    InlineKeyboardButton(
                        "🛑 Terminate Process", callback_data=f"kill:{pfx}"
                    )
                ]
            )
        else:
            buttons.append(
                [
                    InlineKeyboardButton(
                        "❌ Close Wrapper", callback_data=f"exit:{pfx}"
                    )
                ]
            )
        return InlineKeyboardMarkup(buttons)

    def get_queue_notification_keyboard(self):
        """Generates inline keyboard for queue condition approval alert."""
        pfx = f"{self.session_id}"
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🚀 Approve & Start", callback_data=f"start:{pfx}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "❌ Cancel Queue", callback_data=f"exit:{pfx}"
                    )
                ],
            ]
        )

    async def notify_cpu_temperature(self, bot_api, cpu_temp):
        if not self.enable_cpu_temperature_alert or cpu_temp is None:
            return
        if cpu_temp <= self.CPU_TEMPERATURE_ALERT_THRESHOLD:
            self.cpu_temperature_alert_sent = False
            return
        if self.cpu_temperature_alert_sent:
            return

        await bot_api.send_message(
            chat_id=self.chat_id,
            text=(
                "⚠️ <b>High CPU Temperature</b>\n\n"
                f"CPU: {cpu_temp:.0f}°C (threshold: {self.CPU_TEMPERATURE_ALERT_THRESHOLD}°C)"
            ),
            parse_mode=ParseMode.HTML,
        )
        self.cpu_temperature_alert_sent = True

    async def check_queue_condition(self, bot_api):
        if (
            not self.queue_until
            or self.queue_ready
            or getattr(self.process_manager, "has_started", True)
        ):
            return
        from telewrapperz.queue import QueueConditionError, condition_is_met

        metrics = (
            self.system_monitor.get_metrics()
            if hasattr(self.system_monitor, "get_metrics")
            else {
                "cpu": self.system_monitor.get_stats()[0],
                "memory": self.system_monitor.get_stats()[1],
            }
        )
        try:
            met = condition_is_met(self.queue_until, metrics)
        except QueueConditionError as exc:
            self.queue_ready = True
            await bot_api.send_message(chat_id=self.chat_id, text=f"❌ {exc}")
            return
        if met:
            self.queue_ready = True
            if not self.queue_notification_sent:
                now_time = datetime.now().strftime("%H:%M:%S")
                safe_cmd = html.escape(self.command)
                safe_cond = html.escape(self.queue_until)
                await bot_api.send_message(
                    chat_id=self.chat_id,
                    text=(
                        "🔔 <b>Queue Condition Met</b>\n\n"
                        "Resources required to execute the command are now available!\n"
                        f"⚙️ <code>{safe_cmd}</code>\n"
                        f"⏳ Condition met: <code>{safe_cond}</code>\n"
                        f"🕒 Last Update: <code>{now_time}</code>\n\n"
                        "Click the button below to approve and start the command."
                    ),
                    parse_mode=ParseMode.HTML,
                    reply_markup=self.get_queue_notification_keyboard(),
                )
                self.queue_notification_sent = True

    def get_completion_keyboard(self):
        """Generates inline keyboard for job completion notification."""
        pfx = f"{self.session_id}"
        buttons = []
        if self.log_file_path and os.path.exists(self.log_file_path):
            buttons.append(
                [
                    InlineKeyboardButton(
                        "📄 Download Log",
                        callback_data=f"download_log:{pfx}",
                    )
                ]
            )
        buttons.append(
            [
                InlineKeyboardButton(
                    "❌ Close Wrapper",
                    callback_data=f"exit:{pfx}",
                )
            ]
        )
        return InlineKeyboardMarkup(buttons)

    def build_completion_text(self):
        """Constructs text for the job completion notification."""
        now = datetime.now()
        duration = str(now - self.start_time).split(".")[0]
        now_time = now.strftime("%H:%M:%S")
        return_code = getattr(self.process_manager, "return_code", None)

        if return_code == 0:
            title = "✅ <b>Job Completed Successfully!</b>"
            status_text = "✅ Success (Exit: 0)"
        else:
            title = "❌ <b>Job Failed!</b>"
            status_text = f"❌ Error (Exit: {return_code})"

        safe_hostname = html.escape(self.hostname)
        safe_command = html.escape(self.command)

        raw_logs = (
            self.process_manager.log_buffer.get_lines().rstrip("\n")
            if hasattr(self.process_manager, "log_buffer")
            else ""
        )
        clean_logs = strip_ansi(raw_logs)

        header = (
            f"{title}\n\n"
            f"🖥 <b>{safe_hostname}</b> (PID: {self.pid})\n"
            f"⚙️ <code>{safe_command}</code>\n\n"
            f"Status: {status_text}\n"
            f"⏱️ Duration: {duration}\n"
            f"🕒 Finished at: {now_time}\n"
        )

        lines = [line for line in clean_logs.splitlines() if line.strip()]
        tail_lines = "\n".join(lines[-10:]) if lines else ""

        if tail_lines:
            header += "\n📜 <b>Recent Log:</b>\n"
            safe_logs = html.escape(tail_lines)
            max_len = 4096
            overhead = len(header) + len("<pre></pre>") + 20
            available_chars = max_len - overhead
            if len(safe_logs) > available_chars:
                trunc_msg = "\n...[truncated]...\n"
                keep_len = available_chars - len(trunc_msg)
                if keep_len > 0:
                    safe_logs = trunc_msg + safe_logs[-keep_len:]
                else:
                    safe_logs = trunc_msg
            return f"{header}<pre>{safe_logs}</pre>"

        return header

    async def notify_completion(self, bot_api):
        if not self.enable_completion_notification:
            return False
        if self.completion_notification_sent:
            return False
        if not getattr(self.process_manager, "has_started", False):
            return False
        if getattr(self.process_manager, "is_running", False):
            return False
        if getattr(self.process_manager, "return_code", None) is None:
            return False

        self.completion_notification_sent = True

        msg_text = self.build_completion_text()
        keyboard = self.get_completion_keyboard()

        try:
            await bot_api.send_message(
                chat_id=self.chat_id,
                text=msg_text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
        except Exception as e:
            print(f"Failed to send completion notification: {e}")

        if (
            self.process_manager.return_code != 0
            and self.log_file_path
            and os.path.exists(self.log_file_path)
        ):
            try:
                with open(self.log_file_path, "rb") as f:
                    await bot_api.send_document(
                        chat_id=self.chat_id,
                        document=f,
                        filename=os.path.basename(self.log_file_path),
                        caption=f"📄 Failure Log: {os.path.basename(self.log_file_path)}",
                    )
            except Exception as e:
                print(f"Failed to send failure log document: {e}")

        return True

    async def check_completion(self, bot_api):
        if (
            self.enable_completion_notification
            and not self.completion_notification_sent
            and getattr(self.process_manager, "has_started", False)
            and not getattr(self.process_manager, "is_running", False)
            and getattr(self.process_manager, "return_code", None) is not None
        ):
            await self.notify_completion(bot_api)

    async def telegram_updater(self, app):
        """Background task to update dashboard message periodically."""
        try:
            initial_text = self.build_dashboard_text()
            msg = await app.bot.send_message(
                chat_id=self.chat_id,
                text=initial_text,
                parse_mode=ParseMode.HTML,
                reply_markup=self.get_keyboard(),
            )
            self.dashboard_message_id = msg.message_id
            self.last_message_text = initial_text
        except Exception as e:
            print(f"Telegram Init Error: {e}")
            return

        while True:
            await asyncio.sleep(
                self.queue_check_interval
                if not self.queue_ready
                else self.update_interval
            )
            if self.shutdown_signal:
                break

            try:
                _, _, cpu_temp, _ = self.system_monitor.get_stats()
                await self.check_queue_condition(app.bot)
                await self.notify_cpu_temperature(app.bot, cpu_temp)
                await self.check_completion(app.bot)
                if self.dashboard_message_id:
                    await self.update_dashboard_message(app.bot)

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Critical Loop Error: {e}")
                await asyncio.sleep(1)

    async def handle_button(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        query = update.callback_query

        try:
            data = query.data.split(":")
            action = data[0]
            target_session = data[1]

            if target_session != self.session_id:
                await query.answer(
                    "Session expired or invalid", show_alert=True
                )
                return

            await query.answer()

            if action == "refresh":
                await self.update_dashboard_message(context.bot, force=True)

            elif action == "start":
                if getattr(self.process_manager, "has_started", True):
                    await query.answer(
                        "Command already started", show_alert=True
                    )
                    return
                if not self.queue_ready:
                    await query.answer(
                        "Resources not yet available", show_alert=True
                    )
                    return
                if self.start_process:
                    await self.start_process()
                if (
                    query.message
                    and query.message.message_id != self.dashboard_message_id
                ):
                    try:
                        safe_cmd = html.escape(self.command)
                        now_time = datetime.now().strftime("%H:%M:%S")
                        await query.edit_message_text(
                            f"✅ <b>Command approved and started!</b>\n⚙️ <code>{safe_cmd}</code>\n🕒 Last Update: <code>{now_time}</code>",
                            parse_mode=ParseMode.HTML,
                        )
                    except Exception:
                        pass
                await self.update_dashboard_message(context.bot, force=True)

            elif action == "kill":
                self.process_manager.terminate()
                await self.update_dashboard_message(context.bot, force=True)

            elif action == "download_log":
                if self.log_file_path and os.path.exists(self.log_file_path):
                    with open(self.log_file_path, "rb") as f:
                        await context.bot.send_document(
                            chat_id=self.chat_id,
                            document=f,
                            filename=os.path.basename(self.log_file_path),
                        )

            elif action == "exit":
                self.shutdown_signal = True
                await query.edit_message_text(
                    f"🛑 Wrapper on {self.hostname} terminated."
                )
        except Exception as e:
            print(f"ERROR in handle_button: {e}")
            self.process_manager.log_buffer.append(
                f"[Wrapper Error] Button handler: {e}\n"
            )
