"""Business space: the shared data for ONE business. All agents read it.

It holds: brand brief, connected accounts, content calendar, media library, learnings.
Today it is a folder of JSON files: data/businesses/<tenant_id>/
TODO (plan "Tech choices"): Postgres with row-level security by tenant_id,
a vector store for learnings, and R2 for the media library.
"""
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import config


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def parse_time(value: str) -> datetime:
    t = datetime.fromisoformat(value)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


class BusinessSpace:
    def __init__(self, tenant_id: str):
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", tenant_id):
            raise ValueError("tenant_id: use only letters, numbers, - and _")
        self.tenant_id = tenant_id
        self.root: Path = config.DATA_DIR / tenant_id
        self.media_library: Path = self.root / "media_library"
        self.media_library.mkdir(parents=True, exist_ok=True)

    # -- file helpers --
    def _read(self, name: str, default):
        path = self.root / f"{name}.json"
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    def _write(self, name: str, data) -> None:
        path = self.root / f"{name}.json"
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # -- brand brief --
    def get_brand_brief(self) -> dict | None:
        return self._read("brand_brief", None)

    def save_brand_brief(self, brief: dict) -> None:
        brief = dict(brief, updated_at=now_utc().isoformat())
        self._write("brand_brief", brief)

    # -- connected accounts --
    def get_connected_accounts(self) -> dict:
        return self._read("connected_accounts", {})

    def save_connected_accounts(self, accounts: dict) -> None:
        self._write("connected_accounts", accounts)

    def connected_platforms(self) -> list[str]:
        return [p for p, a in self.get_connected_accounts().items() if a.get("status") == "connected"]

    # -- content calendar (one record per post) --
    def get_calendar(self) -> list[dict]:
        return self._read("content_calendar", [])

    def save_calendar(self, calendar: list[dict]) -> None:
        calendar.sort(key=lambda p: p.get("scheduled_at", ""))
        self._write("content_calendar", calendar)

    def add_posts(self, posts: list[dict]) -> list[dict]:
        calendar = self.get_calendar()
        for post in posts:
            post.setdefault("id", "post-" + uuid.uuid4().hex[:8])
            post.setdefault("status", "planned")
            post.setdefault("change_notes", [])
            post["created_at"] = now_utc().isoformat()
            calendar.append(post)
        self.save_calendar(calendar)
        return posts

    def get_post(self, post_id: str) -> dict | None:
        return next((p for p in self.get_calendar() if p["id"] == post_id), None)

    def update_post(self, post_id: str, **changes) -> dict:
        calendar = self.get_calendar()
        for post in calendar:
            if post["id"] == post_id:
                post.update(changes)
                self.save_calendar(calendar)
                return post
        raise KeyError(post_id)

    def posts_with_status(self, *statuses: str) -> list[dict]:
        return [p for p in self.get_calendar() if p.get("status") in statuses]

    # -- media library (one folder per post) --
    def media_folder(self, post_id: str) -> Path:
        folder = self.media_library / post_id
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    # -- learnings --
    def get_learnings(self) -> list[dict]:
        return self._read("learnings", [])

    def add_learnings(self, items: list[str]) -> None:
        learnings = self.get_learnings()
        stamp = now_utc().isoformat()
        learnings.extend({"learning": text, "added_at": stamp} for text in items)
        self._write("learnings", learnings)

    # -- alerts to the owner (shown in the Owner app) --
    def add_alert(self, text: str, phone: bool = True) -> None:
        from .tools import notify  # Telegram / webhook / email, when set in .env.

        sent = notify.send(self.tenant_id, text, phone=phone)
        alerts = self._read("alerts", [])
        alerts.append({"alert": text, "at": now_utc().isoformat(), "sent_by": sent})
        self._write("alerts", alerts)

    # -- content plan (the Planner's plan file, ready for the Creator) --
    def get_content_plan(self) -> dict | None:
        return self._read("content_plan", None)

    def save_content_plan(self, plan: dict) -> Path:
        self._write("content_plan", plan)
        return self.root / "content_plan.json"

    def summary(self) -> dict:
        calendar = self.get_calendar()
        counts: dict[str, int] = {}
        for p in calendar:
            counts[p["status"]] = counts.get(p["status"], 0) + 1
        brief = self.get_brand_brief()
        return {
            "business": self.tenant_id,
            "brand_brief": "ready" if brief else "missing (run the Interviewer)",
            "business_name": (brief or {}).get("business_name"),
            "connected_accounts": self.get_connected_accounts(),
            "content_calendar": counts,
            "learnings": len(self.get_learnings()),
            "content_plan": "content_plan.json" if (self.root / "content_plan.json").exists() else "none yet",
        }
