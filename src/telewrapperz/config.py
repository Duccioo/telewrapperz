import argparse
import configparser
import os
import yaml

DEFAULT_UPDATE_INTERVAL = 5.0


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def load_config():
    """Parses command line arguments and loads configuration files."""
    parser = argparse.ArgumentParser(description="Telegram Command Wrapper")
    parser.add_argument(
        "command", nargs="?", help="The command to execute (wrap in quotes)"
    )
    parser.add_argument("--token", help="Telegram Bot Token")
    parser.add_argument("--chat_id", help="Telegram Chat ID")
    parser.add_argument("--config", help="Path to config file")
    parser.add_argument(
        "--test",
        action="store_true",
        help="Run connection and functionality test",
    )
    parser.add_argument(
        "--log",
        action="store_true",
        help="Save full command output to a local log file",
    )
    parser.add_argument(
        "--show-disk",
        "--disk",
        action="store_true",
        help="Show remaining disk space on the dashboard",
    )
    parser.add_argument(
        "--queue-until",
        help="Queue command until resource condition is met (e.g. ram<80, cpu<50, vram<90)",
    )
    parser.add_argument(
        "--queue-check-interval",
        type=float,
        default=None,
        help="Seconds between queue condition checks",
    )

    args = parser.parse_args()

    token = args.token
    chat_id = args.chat_id
    update_interval = None
    enable_log = args.log
    show_disk = args.show_disk
    enable_cpu_temperature_alert = True
    queue_until = args.queue_until
    queue_check_interval = args.queue_check_interval

    # Parse config file (supports YAML and INI)
    if args.config and os.path.exists(args.config):
        config_path = args.config

        if config_path.endswith((".yaml", ".yml")):
            # YAML config
            try:
                with open(config_path, "r") as f:
                    config = yaml.safe_load(f)
                if config:
                    telegram_config = config.get("telegram", {})
                    if not token:
                        token = telegram_config.get("token")
                    if not chat_id:
                        chat_id = telegram_config.get("chat_id")
                    # Read update_interval from config
                    settings = config.get("settings", {})
                    update_interval = settings.get("update_interval", update_interval)
                    if not enable_log and "enable_log" in settings:
                        enable_log = _as_bool(settings.get("enable_log"))
                    if not show_disk and "show_disk" in settings:
                        show_disk = _as_bool(settings.get("show_disk"))
                    if "enable_cpu_temperature_alert" in settings:
                        enable_cpu_temperature_alert = _as_bool(
                            settings.get("enable_cpu_temperature_alert")
                        )
                    if queue_until is None:
                        queue_until = settings.get("queue_until")
                    if queue_check_interval is None:
                        queue_check_interval = settings.get("queue_check_interval")
            except Exception as e:
                print(f"Error loading YAML config: {e}")
        else:
            # INI config (backward compatibility)
            try:
                ini_config = configparser.ConfigParser()
                ini_config.read(config_path)
                if "Telegram" in ini_config:
                    if not token:
                        token = ini_config["Telegram"].get("token")
                    if not chat_id:
                        chat_id = ini_config["Telegram"].get("chat_id")
                if "Settings" in ini_config:
                    update_interval = ini_config["Settings"].getfloat(
                        "update_interval", fallback=update_interval
                    )
                    if not enable_log and ini_config["Settings"].get("enable_log"):
                        enable_log = ini_config["Settings"].getboolean("enable_log")
                    if not show_disk and ini_config["Settings"].get("show_disk"):
                        show_disk = ini_config["Settings"].getboolean("show_disk")
                    if ini_config["Settings"].get("enable_cpu_temperature_alert"):
                        enable_cpu_temperature_alert = ini_config[
                            "Settings"
                        ].getboolean("enable_cpu_temperature_alert")
                    if queue_until is None:
                        queue_until = ini_config["Settings"].get("queue_until")
                    if queue_check_interval is None:
                        queue_check_interval = ini_config["Settings"].getfloat(
                            "queue_check_interval", fallback=queue_check_interval
                        )
            except Exception as e:
                print(f"Error loading INI config: {e}")

    if not token:
        token = os.environ.get("TELEGRAM_TOKEN")
    if not chat_id:
        chat_id = os.environ.get("TELEGRAM_CHAT_ID") or os.environ.get("CHAT_ID")

    if update_interval is None:
        update_interval = DEFAULT_UPDATE_INTERVAL
    if queue_check_interval is None:
        queue_check_interval = update_interval

    if not enable_log and os.environ.get("TELEWRAPPERZ_ENABLE_LOG"):
        enable_log = _as_bool(os.environ.get("TELEWRAPPERZ_ENABLE_LOG"))

    if not show_disk and os.environ.get("TELEWRAPPERZ_SHOW_DISK"):
        show_disk = _as_bool(os.environ.get("TELEWRAPPERZ_SHOW_DISK"))

    if queue_until:
        from telewrapperz.queue import validate_condition

        validate_condition(queue_until)

    return (
        args.command,
        token,
        chat_id,
        update_interval,
        args.test,
        enable_log,
        enable_cpu_temperature_alert,
        queue_until,
        max(0.1, float(queue_check_interval)),
        show_disk,
    )
