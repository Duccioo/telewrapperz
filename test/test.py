#!/usr/bin/env python3
import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from telewrapperz.bot import TeleWrapperzBot
from telewrapperz.config import load_config
from telewrapperz.logs import LogBuffer, process_terminal_output, strip_ansi
from telewrapperz.process import ProcessManager
from telewrapperz.queue import (
    QueueConditionError,
    condition_is_met,
    parse_condition,
    validate_condition,
)
from telewrapperz.system_stats import SystemMonitor


def section(title):
    print(f"\n=== {title} ===")


def test_strip_ansi():
    data = "\x1b[31mERROR\x1b[0m normal text"
    cleaned = strip_ansi(data)
    assert cleaned == "ERROR normal text"


def test_logbuffer_progress_like_output():
    buf = LogBuffer(max_lines=20)
    chunks = [
        "Start\n",
        "Progress 0%",
        "\rProgress 25%",
        "\rProgress 50%",
        "\rProgress 100%\n",
        "Done\n",
    ]
    for chunk in chunks:
        process_terminal_output(buf, chunk)

    output = buf.get_lines()
    assert "Progress 100%" in output
    assert "Progress 50%" not in output
    assert "Done" in output


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
            return (1.0, 2.0, 42.0, "GPU 0: 65°C | 10%")

        def get_metrics(self):
            return {
                "cpu": 1.0,
                "memory": 2.0,
                "cpu_temp": 42.0,
                "gpu_info": "GPU 0: 65°C | 10%",
                "disk_info": "Disk: 100.0/500.0GB free (20% free)",
            }

    class DummyProcess:
        is_running = True
        return_code = None
        log_buffer = LogBuffer()

    process_terminal_output(DummyProcess.log_buffer, "\r\n\r\n")
    bot = TeleWrapperzBot(
        "token", "chat", "python test.py", DummyProcess(), DummyMonitor(), 5, show_disk=True
    )
    text = bot.build_dashboard_text()
    assert "Starting..." in text
    assert "CPU Temp: 42°C" in text
    assert "GPU 0: 65°C" in text
    assert "Disk: 100.0/500.0GB free" in text
    assert "Last Update:" in text


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
            "  show_disk: true\n"
            "  enable_cpu_temperature_alert: false\n"
        )
        f.flush()
        config_path = f.name

    sys.argv = ["telewrapperz", "--config", config_path, "python test.py"]
    try:
        (
            command,
            token,
            chat_id,
            interval,
            is_test,
            enable_log,
            enable_cpu_temperature_alert,
            queue_until,
            queue_check_interval,
            show_disk,
            notify_on_completion,
        ) = load_config()
    finally:
        sys.argv = original_argv
        Path(config_path).unlink()

    assert command == "python test.py"
    assert token == "token"
    assert chat_id == "chat"
    assert interval == 7
    assert is_test is False
    assert enable_log is True
    assert show_disk is True
    assert enable_cpu_temperature_alert is False
    assert queue_until is None
    assert queue_check_interval == 7
    assert notify_on_completion is True


def test_queue_condition_single():
    assert condition_is_met("ram<80", 90, 79.9)
    assert condition_is_met("cpu<=50", 50, 90)
    assert not condition_is_met("memory<80", 90, 80)
    assert condition_is_met("cpu>10", 15, 50)
    assert condition_is_met("ram>=70", 10, 70)


def test_queue_condition_compound_and_metrics():
    metrics = {
        "cpu": 30.0,
        "memory": 60.0,
        "cpu_temp": 65.0,
        "gpu_util": 40.0,
        "vram": 55.0,
        "disk": 70.0,
        "disk_free": 120.0,
    }
    assert condition_is_met("ram<80 and cpu<50", metrics)
    assert condition_is_met("ram<80, cpu<50, vram<60", metrics)
    assert condition_is_met("cpu<20 or ram<70", metrics)
    assert not condition_is_met("ram<50 and cpu<50", metrics)
    assert condition_is_met("temp<70", metrics)
    assert condition_is_met("vram<=55", metrics)
    assert condition_is_met("disk<80 and disk_free>100", metrics)


def test_queue_condition_validation_and_errors():
    validate_condition("cpu<50")
    validate_condition("ram<=80 and vram<90")
    validate_condition("disk<85 and disk_free>50")

    try:
        validate_condition("unsupported_resource<80")
        assert False, "expected QueueConditionError for invalid resource"
    except QueueConditionError:
        pass

    try:
        validate_condition("invalid_syntax")
        assert False, "expected QueueConditionError for invalid syntax"
    except QueueConditionError:
        pass

    try:
        condition_is_met("gpu<50", {"cpu": 10, "memory": 20})
        assert False, "expected QueueConditionError for missing metric"
    except QueueConditionError:
        pass


def test_system_monitor_metrics():
    monitor = SystemMonitor()
    metrics = monitor.get_metrics()
    assert "cpu" in metrics
    assert "memory" in metrics
    assert "cpu_temp" in metrics
    assert "gpu_info" in metrics
    assert "disk_info" in metrics
    assert "disk" in metrics
    assert "disk_free" in metrics
    stats = monitor.get_stats()
    assert len(stats) == 4
    monitor.close()


async def test_cpu_temperature_alert():
    class DummyMonitor:
        def get_stats(self):
            return (1.0, 2.0, 42.0, "")

        def get_metrics(self):
            return {
                "cpu": 1.0,
                "memory": 2.0,
                "cpu_temp": 42.0,
                "gpu_info": "",
                "disk_info": "Disk: 10GB free",
            }

    class DummyProcess:
        is_running = True
        return_code = None
        log_buffer = LogBuffer()

    class DummyBotApi:
        def __init__(self):
            self.messages = []

        async def send_message(self, **kwargs):
            self.messages.append(kwargs)

    bot = TeleWrapperzBot(
        "token", "chat", "command", DummyProcess(), DummyMonitor(), 5
    )
    api = DummyBotApi()
    await bot.notify_cpu_temperature(api, 91)
    await bot.notify_cpu_temperature(api, 95)
    await bot.notify_cpu_temperature(api, 89)
    await bot.notify_cpu_temperature(api, 92)

    assert len(api.messages) == 2
    assert "91°C" in api.messages[0]["text"]


async def test_bot_queue_lifecycle():
    class DummyProcess:
        def __init__(self):
            self.has_started = False
            self.is_running = False
            self.return_code = None
            self.log_buffer = LogBuffer()

    class DynamicMonitor:
        def __init__(self):
            self.cpu = 90.0
            self.ram = 90.0

        def get_stats(self):
            return (self.cpu, self.ram, 50.0, "")

        def get_metrics(self):
            return {
                "cpu": self.cpu,
                "memory": self.ram,
                "ram": self.ram,
                "cpu_temp": 50.0,
                "gpu_info": "",
                "disk_info": "Disk: 200GB free",
            }

    class DummyBotApi:
        def __init__(self):
            self.messages = []
            self.edited = []

        async def send_message(self, **kwargs):
            self.messages.append(kwargs)
            return type("Msg", (), {"message_id": len(self.messages)})()

        async def edit_message_text(self, **kwargs):
            self.edited.append(kwargs)

    class DummyQuery:
        def __init__(self, data, session_id):
            self.data = f"{data}:{session_id}"
            self.message = type("Msg", (), {"message_id": 999})()
            self.answered = False
            self.alerts = []

        async def answer(self, text=None, show_alert=False):
            self.answered = True
            if show_alert and text:
                self.alerts.append(text)

        async def edit_message_text(self, text, **kwargs):
            pass

    proc = DummyProcess()
    monitor = DynamicMonitor()
    started = False

    async def start_proc():
        nonlocal started
        started = True
        proc.has_started = True
        proc.is_running = True

    bot = TeleWrapperzBot(
        token="token",
        chat_id="chat",
        command="python train.py",
        process_manager=proc,
        system_monitor=monitor,
        update_interval=5,
        queue_until="ram<80 and cpu<50",
        start_process=start_proc,
        show_disk=True,
    )
    api = DummyBotApi()

    # Initial state
    assert not bot.queue_ready
    text = bot.build_dashboard_text()
    assert "Queued" in text
    assert "Waiting" in text
    assert "Disk: 200GB free" in text

    # Condition check while resources are high -> remains queued
    await bot.check_queue_condition(api)
    assert not bot.queue_ready
    assert len(api.messages) == 0

    # Resources drop below thresholds
    monitor.cpu = 40.0
    monitor.ram = 60.0
    await bot.check_queue_condition(api)
    assert bot.queue_ready
    assert len(api.messages) == 1
    assert "Queue Condition Met" in api.messages[0]["text"]

    # Dashboard text reflects waiting for approval state
    text_ready = bot.build_dashboard_text()
    assert "Pending Approval" in text_ready
    assert "Verified" in text_ready

    # Simulate Telegram button click to start command
    context = type("Context", (), {"bot": api})()
    query = DummyQuery("start", bot.session_id)
    update = type("Update", (), {"callback_query": query})()

    await bot.handle_button(update, context)
    assert started
    assert proc.has_started
    assert proc.is_running

    # Second click should be rejected
    query2 = DummyQuery("start", bot.session_id)
    update2 = type("Update", (), {"callback_query": query2})()
    await bot.handle_button(update2, context)
    assert "Command already started" in query2.alerts


def test_config_notify_on_completion_flag_and_yaml():
    original_argv = sys.argv[:]
    # Test --no-notify flag
    sys.argv = ["telewrapperz", "--token", "tok", "--chat_id", "123", "--no-notify", "cmd"]
    try:
        cfg = load_config()
        assert cfg[10] is False
    finally:
        sys.argv = original_argv

    # Test YAML notify_on_completion: false
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(
            "telegram:\n"
            "  token: tok\n"
            "  chat_id: '123'\n"
            "settings:\n"
            "  notify_on_completion: false\n"
        )
        f.flush()
        yaml_path = f.name
    sys.argv = ["telewrapperz", "--config", yaml_path, "cmd"]
    try:
        cfg = load_config()
        assert cfg[10] is False
    finally:
        sys.argv = original_argv
        Path(yaml_path).unlink()


async def test_bot_notify_completion_success():
    class DummyMonitor:
        def get_stats(self):
            return (1.0, 2.0, 42.0, "")

    class DummyProcess:
        has_started = True
        is_running = False
        return_code = 0
        log_buffer = LogBuffer()

    class DummyBotApi:
        def __init__(self):
            self.messages = []
            self.documents = []

        async def send_message(self, **kwargs):
            self.messages.append(kwargs)
            return type("Msg", (), {"message_id": len(self.messages)})()

        async def send_document(self, **kwargs):
            self.documents.append(kwargs)

    proc = DummyProcess()
    process_terminal_output(proc.log_buffer, "Step 1\nDone!\n")
    bot = TeleWrapperzBot(
        "token", "chat", "python train.py", proc, DummyMonitor(), 5
    )
    api = DummyBotApi()

    assert bot.completion_notification_sent is False
    success = await bot.notify_completion(api)
    assert success is True
    assert bot.completion_notification_sent is True
    assert len(api.messages) == 1
    assert len(api.documents) == 0

    msg = api.messages[0]
    assert "Job Completed Successfully!" in msg["text"]
    assert "Exit: 0" in msg["text"]
    assert "python train.py" in msg["text"]
    assert "Done!" in msg["text"]
    assert "Duration:" in msg["text"]
    assert "Finished at:" in msg["text"]
    assert msg["reply_markup"] is not None

    # Calling again does not resend
    success_second = await bot.notify_completion(api)
    assert success_second is False
    assert len(api.messages) == 1


async def test_bot_notify_completion_failure_with_log():
    class DummyMonitor:
        def get_stats(self):
            return (1.0, 2.0, 42.0, "")

    class DummyProcess:
        has_started = True
        is_running = False
        return_code = 1
        log_buffer = LogBuffer()

    class DummyBotApi:
        def __init__(self):
            self.messages = []
            self.documents = []

        async def send_message(self, **kwargs):
            self.messages.append(kwargs)
            return type("Msg", (), {"message_id": len(self.messages)})()

        async def send_document(self, **kwargs):
            self.documents.append(kwargs)

    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
        f.write("Some failure log trace\n")
        f.flush()
        log_path = f.name

    try:
        proc = DummyProcess()
        process_terminal_output(proc.log_buffer, "Error: out of memory\n")
        bot = TeleWrapperzBot(
            "token",
            "chat",
            "python train.py",
            proc,
            DummyMonitor(),
            5,
            log_file_path=log_path,
        )
        api = DummyBotApi()

        success = await bot.notify_completion(api)
        assert success is True
        assert bot.completion_notification_sent is True
        assert len(api.messages) == 1
        assert len(api.documents) == 1

        msg = api.messages[0]
        assert "Job Failed!" in msg["text"]
        assert "Exit: 1" in msg["text"]
        assert "Error: out of memory" in msg["text"]

        doc = api.documents[0]
        assert doc["chat_id"] == "chat"
        assert Path(log_path).name in doc["filename"]
    finally:
        Path(log_path).unlink(missing_ok=True)


async def test_bot_notify_completion_disabled():
    class DummyMonitor:
        def get_stats(self):
            return (1.0, 2.0, 42.0, "")

    class DummyProcess:
        has_started = True
        is_running = False
        return_code = 0
        log_buffer = LogBuffer()

    class DummyBotApi:
        def __init__(self):
            self.messages = []

        async def send_message(self, **kwargs):
            self.messages.append(kwargs)

    proc = DummyProcess()
    bot = TeleWrapperzBot(
        "token",
        "chat",
        "python train.py",
        proc,
        DummyMonitor(),
        5,
        enable_completion_notification=False,
    )
    api = DummyBotApi()

    success = await bot.notify_completion(api)
    assert success is False
    assert bot.completion_notification_sent is False
    assert len(api.messages) == 0


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
    print(f"Src path present: {SRC.exists()}")

    total = 0
    passed = 0

    for name, fn in [
        ("strip_ansi", test_strip_ansi),
        ("logbuffer_progress", test_logbuffer_progress_like_output),
        ("logbuffer_multiline", test_logbuffer_multiline),
        ("logbuffer_pty_crlf", test_logbuffer_pty_crlf),
        ("dashboard_blank_log_fallback", test_dashboard_blank_log_fallback),
        ("config_enable_log_from_yaml", test_config_enable_log_from_yaml),
        (
            "config_notify_on_completion_flag_and_yaml",
            test_config_notify_on_completion_flag_and_yaml,
        ),
        ("queue_condition_single", test_queue_condition_single),
        (
            "queue_condition_compound_and_metrics",
            test_queue_condition_compound_and_metrics,
        ),
        (
            "queue_condition_validation_and_errors",
            test_queue_condition_validation_and_errors,
        ),
        ("system_monitor_metrics", test_system_monitor_metrics),
    ]:
        total += 1
        if run_case(name, fn):
            passed += 1

    for async_name, async_fn in [
        ("process_manager_exit_code", test_process_manager_exit_code),
        ("cpu_temperature_alert", test_cpu_temperature_alert),
        ("bot_queue_lifecycle", test_bot_queue_lifecycle),
        ("bot_notify_completion_success", test_bot_notify_completion_success),
        (
            "bot_notify_completion_failure_with_log",
            test_bot_notify_completion_failure_with_log,
        ),
        ("bot_notify_completion_disabled", test_bot_notify_completion_disabled),
    ]:
        total += 1
        if asyncio.run(run_async_case(async_name, async_fn)):
            passed += 1

    section("Result")
    print(f"Passed: {passed}/{total}")
    if passed != total:
        sys.exit(1)


if __name__ == "__main__":
    main()
