"""Thread-safe, append-only conversation events for one note/run."""
import json
import os
from contextvars import ContextVar
from datetime import datetime, timezone
from threading import Lock

CURRENT_LOG = ContextVar('conversation_log', default=None)


class ConversationLog:
    def __init__(self, path):
        self.path = path
        self.lock = Lock()
        self.sequence = 0
        if self.path.exists():
            raw = self.path.read_bytes()
            for line in raw.decode('utf-8', errors='replace').splitlines():
                try:
                    self.sequence = max(self.sequence, json.loads(line).get('sequence', 0))
                except (ValueError, AttributeError):
                    pass
            if raw and not raw.endswith(b'\n'):
                with self.path.open('ab') as stream:
                    stream.write(b'\n')
        self.secrets = [os.environ[k] for k in ('OPENAI_API_KEY', 'OPENROUTER_API_KEY')
                        if os.environ.get(k)]

    def emit(self, event, **data):
        with self.lock:
            self.sequence += 1
            record = {'sequence': self.sequence,
                      'timestamp': datetime.now(timezone.utc).isoformat(),
                      'event': event, **data}
            line = json.dumps(record, ensure_ascii=False)
            for secret in self.secrets:
                line = line.replace(json.dumps(secret, ensure_ascii=False)[1:-1], '[REDACTED]')
            with self.path.open('a', encoding='utf-8') as stream:
                stream.write(line + '\n')
                stream.flush()


def log_event(event, **data):
    logger = CURRENT_LOG.get()
    if logger is not None:
        logger.emit(event, **data)
