import psutil
import warnings

# Optional pynvml handling for NVIDIA GPU stats
try:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=FutureWarning)
        import pynvml

    PYNVML_INSTALLED = True
except ImportError:
    PYNVML_INSTALLED = False


class SystemMonitor:
    def __init__(self):
        self.gpu_available = False
        if PYNVML_INSTALLED:
            try:
                pynvml.nvmlInit()
                self.gpu_available = True
            except Exception:
                pass

    def get_disk_info(self, path="."):
        """Returns formatted disk usage string and numeric metrics."""
        try:
            usage = psutil.disk_usage(path)
            free_gb = usage.free / (1024**3)
            total_gb = usage.total / (1024**3)
            used_pct = usage.percent
            free_pct = max(0.0, 100.0 - used_pct)
            return (
                f"Disk: {free_gb:.1f}/{total_gb:.1f}GB free ({free_pct:.0f}% free)",
                used_pct,
                free_gb,
            )
        except Exception:
            return "Disk: N/A", None, None

    def get_metrics(self):
        """Collects system metrics in a structured dictionary."""
        cpu = psutil.cpu_percent()
        mem = psutil.virtual_memory().percent
        cpu_temp = None
        try:
            temperatures = psutil.sensors_temperatures()
            values = [
                sensor.current
                for sensors in temperatures.values()
                for sensor in sensors
                if sensor.current is not None
            ]
            if values:
                cpu_temp = max(values)
        except (AttributeError, OSError):
            pass

        disk_info, disk_pct, disk_free_gb = self.get_disk_info()

        gpu_util = None
        vram_percent = None
        gpu_temp = None
        gpu_info = ""
        if self.gpu_available:
            try:
                device_count = pynvml.nvmlDeviceGetCount()
                for i in range(device_count):
                    handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                    util = pynvml.nvmlDeviceGetUtilizationRates(handle).gpu
                    mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                    used_mem_gb = mem_info.used / 1024**3
                    total_mem_gb = mem_info.total / 1024**3
                    vram_pct = (mem_info.used / mem_info.total) * 100
                    temp = pynvml.nvmlDeviceGetTemperature(
                        handle, pynvml.NVML_TEMPERATURE_GPU
                    )
                    gpu_info += (
                        f"GPU {i}: {temp}°C | {util}% | "
                        f"VRAM: {used_mem_gb:.1f}/{total_mem_gb:.1f}GB ({vram_pct:.0f}%)\n"
                    )
                    if gpu_util is None or util > gpu_util:
                        gpu_util = float(util)
                    if vram_percent is None or vram_pct > vram_percent:
                        vram_percent = float(vram_pct)
                    if gpu_temp is None or temp > gpu_temp:
                        gpu_temp = float(temp)
            except Exception:
                gpu_info = "GPU Err"

        return {
            "cpu": cpu,
            "memory": mem,
            "ram": mem,
            "mem": mem,
            "cpu_temp": cpu_temp,
            "temp": cpu_temp,
            "temperature": cpu_temp,
            "disk": disk_pct,
            "disk_used": disk_pct,
            "disk_free": disk_free_gb,
            "disk_free_gb": disk_free_gb,
            "disk_info": disk_info,
            "gpu_util": gpu_util,
            "gpu": gpu_util,
            "vram": vram_percent,
            "gpu_mem": vram_percent,
            "gpu_temp": gpu_temp,
            "gpu_info": gpu_info.strip(),
        }

    def get_stats(self):
        """Collects CPU, RAM, CPU temp, and GPU stats for backward compatibility."""
        metrics = self.get_metrics()
        return (
            metrics["cpu"],
            metrics["memory"],
            metrics["cpu_temp"],
            metrics["gpu_info"],
        )

    def close(self):
        if self.gpu_available:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass
