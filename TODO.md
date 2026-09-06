# TODO

- [ ] Add allowed command profiles in YAML config with Telegram buttons to launch them, preventing arbitrary shell execution.
- [x] Send a final completion notification for success or error including duration, exit code, tail lines, and attached log file on failure.
- [ ] Store local history of recent runs: command, status, duration, and log download.
- [ ] Queue incoming commands when a process is already running.
- [ ] Add `telewrapperz init` to generate configuration and verify token and chat ID.
- [ ] Add configurable alerts for CPU/RAM/GPU thresholds and stalled output timeout.
- [ ] Add a Telegram button to re-run the last command when it terminates with an error.
