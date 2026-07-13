import asyncio
import socket
import uuid
import html
import os
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from telegram.error import BadRequest, RetryAfter, NetworkError, TimedOut
from telewrapperz.logs import strip_ansi, MAX_LOG_LINES


class TeleWrapperzBot:
    def __init__(
        self, token, chat_id, command, process_manager, system_monitor, update_interval, log_file_path=None
    ):
        self.token = token
        self.chat_id = chat_id
        self.command = command
        self.process_manager = process_manager
        self.system_monitor = system_monitor
        self.update_interval = update_interval
        self.log_file_path = log_file_path

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
            # Problemi di rete temporanei, riprova al prossimo ciclo
            pass
        except Exception as e:
            print(f"Telegram Update Error: {e}")

        return False

    def build_dashboard_text(self):
        """Costruisce il messaggio di stato."""
        cpu, mem, gpu_stats = self.system_monitor.get_stats()
        duration = str(datetime.now() - self.start_time).split(".")[0]

        status_icon = (
            "🟢 Running"
            if self.process_manager.is_running
            else (
                f"✅ Done (Exit: {self.process_manager.return_code})"
                if self.process_manager.return_code == 0
                else f"❌ Error (Exit: {self.process_manager.return_code})"
            )
        )

        # Costruzione Log (Clean ANSI & Escape HTML)
        raw_logs = self.process_manager.log_buffer.get_lines().rstrip("\n")
        clean_logs = strip_ansi(raw_logs)
        logs = html.escape(clean_logs)

        if not clean_logs.strip():
            logs = "Starting..."

        # Escape other fields per sicurezza HTML
        safe_hostname = html.escape(self.hostname)
        safe_command = html.escape(self.command)
        safe_gpu_stats = html.escape(gpu_stats) if gpu_stats else ""

        header = (
            f"🖥 <b>{safe_hostname}</b> (PID: {self.pid})\n"
            f"⚙️ <code>{safe_command}</code>\n\n"
            f"Status: {status_icon}\n"
            f"Time: {duration}\n"
            f"CPU: {cpu}% | RAM: {mem}%\n"
        )
        if safe_gpu_stats:
            header += f"<code>{safe_gpu_stats}</code>\n"

        header += f"\n📜 <b>Recent Log (Last {MAX_LOG_LINES}):</b>\n"

        # Calcolo spazio disponibile (Telegram limit 4096)
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
        """Genera la tastiera inline."""
        pfx = f"{self.session_id}"

        buttons = [
            [InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh:{pfx}")],
        ]

        if self.log_file_path and os.path.exists(self.log_file_path):
            buttons.append(
                [InlineKeyboardButton("📄 Scarica Log", callback_data=f"download_log:{pfx}")]
            )

        if self.process_manager.is_running:
            buttons.append(
                [
                    InlineKeyboardButton(
                        "🛑 Termina Processo", callback_data=f"kill:{pfx}"
                    )
                ]
            )
        else:
            buttons.append(
                [InlineKeyboardButton("❌ Chiudi Wrapper", callback_data=f"exit:{pfx}")]
            )
        return InlineKeyboardMarkup(buttons)

    async def telegram_updater(self, app):
        """Task di background per aggiornare il messaggio dashboard."""
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
            print(f"Errore Telegram Init: {e}")
            return

        while True:
            await asyncio.sleep(self.update_interval)
            if self.shutdown_signal:
                break

            try:
                if self.dashboard_message_id:
                    await self.update_dashboard_message(app.bot)

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Critical Loop Error: {e}")
                await asyncio.sleep(1) # Prevent tight crash loop

    async def handle_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        # print(f"DEBUG: Button clicked: {query.data}")

        try:
            data = query.data.split(":")
            action = data[0]
            target_session = data[1]

            if target_session != self.session_id:
                print(f"DEBUG: Session mismatch: {target_session} != {self.session_id}")
                await query.answer("Sessione scaduta o non valida", show_alert=True)
                return

            await query.answer()

            if action == "refresh":
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
                            filename=os.path.basename(self.log_file_path)
                        )

            elif action == "exit":
                self.shutdown_signal = True
                await query.edit_message_text(
                    f"🛑 Wrapper su {self.hostname} terminato."
                )
        except Exception as e:
            print(f"ERROR in handle_button: {e}")
            self.process_manager.log_buffer.append(
                f"[Wrapper Error] Button handler: {e}\n"
            )
