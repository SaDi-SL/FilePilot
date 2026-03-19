import json
from datetime import datetime
from pathlib import Path


class NotificationCenter:
    def __init__(self, storage_path: Path, max_items: int = 200):
        self.storage_path = Path(storage_path)
        self.max_items = max_items
        self.notifications = []
        self._ensure_storage()
        self.load()

    def _ensure_storage(self):
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.storage_path.exists():
            with open(self.storage_path, "w", encoding="utf-8") as file:
                json.dump([], file, indent=2, ensure_ascii=False)

    def load(self):
        self._ensure_storage()

        try:
            with open(self.storage_path, "r", encoding="utf-8") as file:
                data = json.load(file)

            if isinstance(data, list):
                self.notifications = data[: self.max_items]
            else:
                self.notifications = []

        except Exception:
            self.notifications = []

    def save(self):
        self._ensure_storage()

        try:
            with open(self.storage_path, "w", encoding="utf-8") as file:
                json.dump(self.notifications[: self.max_items], file, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def add(self, level: str, title: str, message: str):
        entry = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "level": level.lower().strip(),
            "title": title.strip(),
            "message": message.strip(),
        }

        self.notifications.insert(0, entry)

        if len(self.notifications) > self.max_items:
            self.notifications = self.notifications[: self.max_items]

        self.save()

    def clear(self):
        self.notifications.clear()
        self.save()

    def get_all(self):
        return list(self.notifications)

    def count(self):
        return len(self.notifications)