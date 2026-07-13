import re

MAX_LOG_LINES = 50


def strip_ansi(text):
    """Rimuove codici ANSI (colori, ecc) da una stringa."""
    ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    return ansi_escape.sub("", text)


class LogBuffer:
    """
    Un buffer di log che implementa un parser VT100 minimale.
    Supporta \r, \n, \b e semplici comandi CSI (su, cancella riga)
    per gestire in modo pulito le barre di progresso di `tqdm` e `rich`.
    """
    def __init__(self, max_lines=MAX_LOG_LINES):
        self.max_lines = max_lines
        self.lines = [""]
        self.cursor_y = 0
        self._pending_cr = False

    def write(self, data):
        # 1. Strip color and style codes, and cursor hide/show
        data = re.sub(r'\x1b\[[0-9;]*[mG]', '', data) # colors and cursor horizontal absolute
        data = re.sub(r'\x1b\[\?[0-9]+[lh]', '', data)

        # 2. Tokenize into control sequences and text
        pattern = re.compile(r'(\r|\n|\b|\x1b\[[0-9]*[A-Z])')
        parts = pattern.split(data)

        for part in parts:
            if not part:
                continue

            if self._pending_cr:
                self._pending_cr = False
                if part == '\n':
                    self._newline()
                    continue
                self._carriage_return()

            if part == '\r':
                # Delay handling so CRLF from PTYs is treated as a normal newline.
                self._pending_cr = True
            elif part == '\n':
                self._newline()
            elif part == '\b':
                self.lines[self.cursor_y] = self.lines[self.cursor_y][:-1]
            elif part.startswith('\x1b['):
                char = part[-1]
                if char == 'A': # Cursor Up
                    n = 1
                    m = re.match(r'\x1b\[(\d+)A', part)
                    if m: n = int(m.group(1))
                    self.cursor_y = max(0, self.cursor_y - n)
                elif char == 'K': # Erase Line
                    self.lines[self.cursor_y] = ""
                # Ignora le altre sequenze CSI
            else:
                self.lines[self.cursor_y] += part

    def _carriage_return(self):
        # Carriage return overwrites the current line from the start.
        self.lines[self.cursor_y] = ""

    def _newline(self):
        self.cursor_y += 1
        if self.cursor_y >= len(self.lines):
            self.lines.append("")

        # Prevenire memoria infinita (teniamo un po' di buffer in più per sicurezza)
        if len(self.lines) > self.max_lines * 2:
            self.lines = self.lines[-self.max_lines:]
            self.cursor_y = len(self.lines) - 1

    def get_lines(self):
        """Restituisce le ultime N righe come singola stringa."""
        return "\n".join(self.lines[-self.max_lines:])

    def __iter__(self):
        # Compatibility with the old deque-based interface
        yield self.get_lines()

    def append(self, text):
        # Compatibility with the old deque-based interface
        self.write(text)


def process_terminal_output(log_buffer, data):
    """
    Processa l'output del terminale delegando al LogBuffer VT100-aware.
    """
    log_buffer.write(data)
