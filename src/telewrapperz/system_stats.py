import psutil
import warnings

# Gestione opzionale pynvml
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

    def get_stats(self):
        """Raccoglie info su CPU, RAM e GPU."""
        cpu = psutil.cpu_percent()
        mem = psutil.virtual_memory().percent

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
                    vram_percent = (mem_info.used / mem_info.total) * 100
                    gpu_info += f"GPU {i}: {util}% | VRAM: {used_mem_gb:.1f}/{total_mem_gb:.1f}GB ({vram_percent:.0f}%)\n"
            except Exception:
                gpu_info = "GPU Err"

        return cpu, mem, gpu_info.strip()

    def close(self):
        if self.gpu_available:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass
