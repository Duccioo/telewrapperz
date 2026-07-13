#!/usr/bin/env python3
import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from telewrapperz.logs import LogBuffer, process_terminal_output, strip_ansi
from telewrapperz.process import ProcessManager
from telewrapperz.bot import TeleWrapperzBot
from telewrapperz.config import load_config


def section(title):
    print(f"\n=== {title} ===")


def test_strip_ansi():
    data = "\x1b[31mERRORE\x1b[0m testo normale"
    cleaned = strip_ansi(data)
    assert cleaned == "ERRORE testo normale"


def test_logbuffer_progress_like_output():
    buf = LogBuffer(max_lines=20)
    chunks = [
        "Start\n",
        "Progress 0%",
        "\rProgress 25%",
        "\rProgress 50%",
        "\rProgress 100%\n",
        "Fine\n",
    ]
    for chunk in chunks:
        process_terminal_output(buf, chunk)

    output = buf.get_lines()
    assert "Progress 100%" in output
    assert "Progress 50%" not in output
    assert "Fine" in output


def test_logbuffer_multiline():
    buf = LogBuffer(max_lines=20)
    process_terminal_output(buf, "Loss: 0.1234\n")
    process_terminal_output(buf, "Epoch 1/3\nEpoch 2/3\n")

    output = buf.get_lines()
    assert "Loss: 0.1234" in output
    assert "Epoch 2/3" in output


def test_logbuffer_pty_crlf():
    buf = LogBuffer(max_lines=20)
    process_terminal_output(buf, "123\r\n456\r\n")

    output = buf.get_lines()
    assert "123" in output
    assert "456" in output


async def test_process_manager_exit_code():
    buf = LogBuffer(max_lines=50)
    py = sys.executable
    command = f'"{py}" -c "print(123);print(456)"'

    manager = ProcessManager(command, str(ROOT), buf)
    await manager.run()

    assert manager.return_code == 0
    assert manager.is_running is False
    output = buf.get_lines()
    assert "123" in output
    assert "456" in output


def test_dashboard_blank_log_fallback():
    class DummyMonitor:
        def get_stats(self):
            return (1.0, 2.0, "")

    class DummyProcess:
        is_running = True
        return_code = None
        log_buffer = LogBuffer()

    process_terminal_output(DummyProcess.log_buffer, "\r\n\r\n")
    bot = TeleWrapperzBot("token", "chat", "python test.py", DummyProcess(), DummyMonitor(), 5)
    text = bot.build_dashboard_text()
    assert "Starting..." in text


def test_config_enable_log_from_yaml():
    original_argv = sys.argv[:]
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(
            "telegram:\n"
            "  token: token\n"
            "  chat_id: chat\n"
            "settings:\n"
            "  update_interval: 7\n"
            "  enable_log: true\n"
        )
        f.flush()
        config_path = f.name

    sys.argv = ["telewrapperz", "--config", config_path, "python test.py"]
    try:
        command, token, chat_id, interval, is_test, enable_log = load_config()
    finally:
        sys.argv = original_argv
        Path(config_path).unlink()

    assert command == "python test.py"
    assert token == "token"
    assert chat_id == "chat"
    assert interval == 7
    assert is_test is False
    assert enable_log is True


def run_case(name, fn):
    try:
        fn()
        print(f"[OK] {name}")
        return True
    except Exception as e:
        print(f"[FAIL] {name}: {e}")
        return False


async def run_async_case(name, fn):
    try:
        await fn()
        print(f"[OK] {name}")
        return True
    except Exception as e:
        print(f"[FAIL] {name}: {e}")
        return False


def main():
    section("Telewrapperz local smoke tests")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Platform: {sys.platform}")
    print(f"Repo: {ROOT}")
    print(f"Src path presente: {SRC.exists()}")

    total = 0
    passed = 0

    for name, fn in [
        ("strip_ansi", test_strip_ansi),
        ("logbuffer_progress", test_logbuffer_progress_like_output),
        ("logbuffer_multiline", test_logbuffer_multiline),
        ("logbuffer_pty_crlf", test_logbuffer_pty_crlf),
        ("dashboard_blank_log_fallback", test_dashboard_blank_log_fallback),
        ("config_enable_log_from_yaml", test_config_enable_log_from_yaml),
    ]:
        total += 1
        if run_case(name, fn):
            passed += 1

    total += 1
    if asyncio.run(run_async_case("process_manager_exit_code", test_process_manager_exit_code)):
        passed += 1

    section("Risultato")
    print(f"Passati: {passed}/{total}")
    if passed != total:
        sys.exit(1)


if __name__ == "__main__":
    main()
