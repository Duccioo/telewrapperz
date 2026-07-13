import asyncio
import os
import sys
import platform
from telewrapperz.logs import process_terminal_output

# Rileva sistema operativo
IS_WINDOWS = platform.system() == "Windows"

# Moduli Unix-only (PTY per terminal emulation)
if not IS_WINDOWS:
    import pty
    import select


class ProcessManager:
    def __init__(self, command, working_dir, log_buffer, log_file_path=None):
        self.command = command
        self.working_dir = working_dir
        self.log_buffer = log_buffer
        self.log_file_path = log_file_path
        self.process = None
        self.is_running = True
        self.return_code = None

        # Inizializza il file log
        if self.log_file_path:
            with open(self.log_file_path, "w", encoding="utf-8") as f:
                f.write(f"--- Telewrapperz Log Started ---\nCommand: {self.command}\n\n")

    def _write_to_log_file(self, decoded_text):
        if self.log_file_path:
            with open(self.log_file_path, "a", encoding="utf-8") as f:
                f.write(decoded_text)

    async def run(self):
        """Esegue il comando utente e cattura l'output."""
        if IS_WINDOWS:
            await self._run_process_windows()
        else:
            await self._run_process_unix()

    async def _run_process_windows(self):
        """Implementazione Windows usando subprocess standard con PIPE."""
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        self.process = await asyncio.create_subprocess_shell(
            self.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=self.working_dir,
            env=env,
        )

        async def read_output():
            while True:
                try:
                    # Leggi in chunk invece che per riga per non bloccare le progress bar
                    chunk = await self.process.stdout.read(4096)
                    if not chunk:
                        break
                    decoded = chunk.decode("utf-8", errors="replace")
                    process_terminal_output(self.log_buffer, decoded)
                    self._write_to_log_file(decoded)
                    sys.stdout.write(decoded)
                    sys.stdout.flush()
                except Exception:
                    break

        await read_output()
        self.return_code = await self.process.wait()
        self.is_running = False

    async def _run_process_unix(self):
        """Implementazione Unix/macOS usando PTY per terminal emulation."""
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        # Usa PTY per forzare line-buffered output (simula un terminale reale)
        master_fd, slave_fd = pty.openpty()

        try:
            self.process = await asyncio.create_subprocess_shell(
                self.command,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=self.working_dir,
                env=env,
            )

            # Chiudi il lato slave nel processo padre
            os.close(slave_fd)

            # Task per attendere la terminazione del processo in background
            wait_task = asyncio.create_task(self.process.wait())

            while True:
                # Usa select per non bloccare
                readable, _, _ = select.select([master_fd], [], [], 0.1)

                if readable:
                    try:
                        data = os.read(master_fd, 4096)
                        if not data:
                            break
                        decoded = data.decode("utf-8", errors="replace")
                        process_terminal_output(self.log_buffer, decoded)
                        self._write_to_log_file(decoded)
                        sys.stdout.write(decoded)
                        sys.stdout.flush()
                    except OSError:
                        break

                # Controlla se il processo è terminato
                if wait_task.done():
                    # Leggi eventuale output rimanente
                    try:
                        while True:
                            readable, _, _ = select.select([master_fd], [], [], 0.1)
                            if not readable:
                                break
                            data = os.read(master_fd, 4096)
                            if not data:
                                break
                            decoded = data.decode("utf-8", errors="replace")
                            process_terminal_output(self.log_buffer, decoded)
                            self._write_to_log_file(decoded)
                            sys.stdout.write(decoded)
                            sys.stdout.flush()
                    except OSError:
                        pass
                    break

                # Yield per permettere ad altri task di eseguire
                await asyncio.sleep(0.01)

        finally:
            try:
                os.close(master_fd)
            except OSError:
                pass

        self.return_code = await wait_task
        self.is_running = False

    def terminate(self):
        if self.process and self.is_running:
            try:
                self.process.terminate()
                self.log_buffer.append("\n[WRAPPER] Sent SIGTERM to process...\n")
            except Exception as e:
                self.log_buffer.append(f"\n[WRAPPER] Error killing: {e}\n")
