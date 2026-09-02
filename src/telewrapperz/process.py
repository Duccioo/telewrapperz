import asyncio
import os
import platform
import sys
from telewrapperz.logs import process_terminal_output

# Detect operating system
IS_WINDOWS = platform.system() == "Windows"

# Unix-only modules (PTY for terminal emulation)
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
        self.is_running = False
        self.has_started = False
        self.return_code = None

        # Initialize log file
        if self.log_file_path:
            with open(self.log_file_path, "w", encoding="utf-8") as f:
                f.write(f"--- Telewrapperz Log Started ---\nCommand: {self.command}\n\n")

    def _write_to_log_file(self, decoded_text):
        if self.log_file_path:
            with open(self.log_file_path, "a", encoding="utf-8") as f:
                f.write(decoded_text)

    async def run(self):
        """Executes user command and streams output."""
        if self.has_started:
            return
        self.has_started = True
        self.is_running = True
        if IS_WINDOWS:
            await self._run_process_windows()
        else:
            await self._run_process_unix()

    async def _run_process_windows(self):
        """Windows implementation using subprocess with PIPE."""
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
                    # Read in chunks to preserve progress bar updates
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
        """Unix/macOS implementation using PTY for native terminal emulation."""
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        # Use PTY for line-buffered terminal emulation
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

            # Close slave side in parent process
            os.close(slave_fd)

            # Background task to wait for process termination
            wait_task = asyncio.create_task(self.process.wait())

            while True:
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

                if wait_task.done():
                    # Drain remaining output
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
