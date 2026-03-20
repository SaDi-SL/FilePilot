"""
calendar_integration.py — Calendar reminder integration for FilePilot.

Supports:
    - Windows Calendar (via .ics file — syncs with iPhone, Android, Outlook)
    - Google Calendar (via Google Calendar API)

Usage:
    from app.calendar_integration import CalendarManager
    mgr = CalendarManager()
    mgr.add_reminder("Invoice due", "2025-03-15", "Pay invoice #1234", days_before=7)
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


# ── ICS file creator (works with ALL calendars) ───────────────────────────────

def _create_ics(
    title: str,
    date_str: str,
    description: str,
    remind_days_before: int = 1,
    filename: str = "",
) -> str:
    """Generate an .ics calendar file content."""
    try:
        event_date = datetime.strptime(date_str, "%Y-%m-%d")
    except Exception:
        event_date = datetime.now() + timedelta(days=7)

    remind_date = event_date - timedelta(days=remind_days_before)
    uid = f"filepilot-{event_date.strftime('%Y%m%d')}-{abs(hash(title))}"
    now = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    event_str = event_date.strftime("%Y%m%d")
    remind_str = remind_date.strftime("%Y%m%dT090000")

    desc = description.replace("\n", "\\n").replace(",", "\\,")
    file_note = f"\\nFile: {filename}" if filename else ""

    return f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//FilePilot//FilePilot Calendar//EN
CALSCALE:GREGORIAN
METHOD:PUBLISH
BEGIN:VEVENT
UID:{uid}@filepilot
DTSTAMP:{now}
DTSTART;VALUE=DATE:{event_str}
DTEND;VALUE=DATE:{event_str}
SUMMARY:{title}
DESCRIPTION:{desc}{file_note}
BEGIN:VALARM
TRIGGER:-P{remind_days_before}D
ACTION:DISPLAY
DESCRIPTION:Reminder: {title}
END:VALARM
END:VEVENT
END:VCALENDAR"""


# ── Windows Calendar ──────────────────────────────────────────────────────────

class WindowsCalendarProvider:
    """
    Creates .ics files and opens them with the default calendar app.
    Works with: Windows Calendar, Outlook, Thunderbird, iPhone (iCloud), Android.
    """

    def __init__(self, ics_output_dir: Path | None = None) -> None:
        from app.config_loader import get_runtime_base_dir
        self.output_dir = ics_output_dir or (get_runtime_base_dir() / "reminders")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def add_reminder(
        self,
        title: str,
        date_str: str,
        description: str,
        remind_days_before: int = 1,
        filename: str = "",
        auto_open: bool = True,
    ) -> tuple[bool, str]:
        """
        Create .ics file and optionally open it.
        Returns (success, message).
        """
        try:
            ics_content = _create_ics(title, date_str, description,
                                       remind_days_before, filename)

            safe_title = "".join(c for c in title if c.isalnum() or c in " -_")[:40]
            ics_path = self.output_dir / f"{safe_title}_{date_str}.ics"
            ics_path.write_text(ics_content, encoding="utf-8")

            if auto_open:
                os.startfile(str(ics_path))

            logger.info(f"ICS reminder created: {ics_path.name}")
            return True, f"Reminder added to calendar: {title} on {date_str}"

        except Exception as e:
            logger.error(f"ICS creation failed: {e}")
            return False, f"Failed to create reminder: {e}"

    def list_reminders(self) -> list[dict]:
        """List existing .ics reminder files."""
        reminders = []
        for f in self.output_dir.glob("*.ics"):
            reminders.append({
                "name": f.stem,
                "path": str(f),
                "created": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d"),
            })
        return sorted(reminders, key=lambda x: x["created"], reverse=True)


# ── Google Calendar ───────────────────────────────────────────────────────────

class GoogleCalendarProvider:
    """
    Adds events to Google Calendar via API.
    Requires: google-auth, google-api-python-client
    Setup: OAuth2 credentials from Google Cloud Console.
    """

    def __init__(self, credentials_path: str = "") -> None:
        self.credentials_path = credentials_path
        self._service = None

    def is_configured(self) -> bool:
        return bool(self.credentials_path) and Path(self.credentials_path).exists()

    def _get_service(self):
        if self._service:
            return self._service
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build

            SCOPES = ["https://www.googleapis.com/auth/calendar"]
            creds = None
            token_path = Path(self.credentials_path).parent / "token.json"

            if token_path.exists():
                creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    flow = InstalledAppFlow.from_client_secrets_file(
                        self.credentials_path, SCOPES
                    )
                    creds = flow.run_local_server(port=0)
                token_path.write_text(creds.to_json())

            self._service = build("calendar", "v3", credentials=creds)
            return self._service

        except ImportError:
            raise RuntimeError(
                "Google Calendar requires: pip install google-auth google-auth-oauthlib google-api-python-client"
            )

    def add_reminder(
        self,
        title: str,
        date_str: str,
        description: str,
        remind_days_before: int = 1,
        filename: str = "",
        auto_open: bool = True,
    ) -> tuple[bool, str]:
        if not self.is_configured():
            return False, "Google Calendar credentials not configured."

        try:
            service = self._get_service()
            file_note = f"\n\nFile: {filename}" if filename else ""

            event = {
                "summary": title,
                "description": description + file_note,
                "start": {"date": date_str},
                "end":   {"date": date_str},
                "reminders": {
                    "useDefault": False,
                    "overrides": [
                        {"method": "popup",  "minutes": remind_days_before * 24 * 60},
                        {"method": "email",  "minutes": remind_days_before * 24 * 60},
                    ],
                },
            }

            result = service.events().insert(calendarId="primary", body=event).execute()
            link = result.get("htmlLink", "")
            logger.info(f"Google Calendar event created: {link}")

            if auto_open and link:
                import webbrowser
                webbrowser.open(link)

            return True, f"Event added to Google Calendar: {title}"

        except Exception as e:
            logger.error(f"Google Calendar error: {e}")
            return False, f"Google Calendar error: {e}"


# ── Unified CalendarManager ───────────────────────────────────────────────────

class CalendarManager:
    """
    Unified calendar interface supporting Windows (.ics) and Google Calendar.
    User can choose preferred provider from Settings.
    """

    def __init__(
        self,
        provider: str = "windows",
        google_credentials_path: str = "",
    ) -> None:
        self.provider_name = provider
        self._windows = WindowsCalendarProvider()
        self._google  = GoogleCalendarProvider(credentials_path=google_credentials_path)

    def add_reminder(
        self,
        title: str,
        date_str: str,
        description: str,
        remind_days_before: int = 1,
        filename: str = "",
        auto_open: bool = True,
    ) -> tuple[bool, str]:
        """Add a reminder to the configured calendar."""
        if self.provider_name == "google" and self._google.is_configured():
            return self._google.add_reminder(
                title, date_str, description, remind_days_before, filename, auto_open
            )
        # Default: Windows .ics
        return self._windows.add_reminder(
            title, date_str, description, remind_days_before, filename, auto_open
        )

    def add_reminders_from_analysis(
        self,
        analysis,   # DocumentAnalysis
        auto_open: bool = True,
    ) -> list[tuple[bool, str]]:
        """Add all key dates from a DocumentAnalysis as calendar reminders."""
        results = []
        for date_info in analysis.key_dates:
            ok, msg = self.add_reminder(
                title=f"{date_info.label} — {analysis.filename}",
                date_str=date_info.date,
                description=date_info.description,
                remind_days_before=date_info.remind_days_before,
                filename=analysis.filename,
                auto_open=auto_open,
            )
            results.append((ok, msg))
        return results

    def list_reminders(self) -> list[dict]:
        """List existing reminders (Windows only for now)."""
        return self._windows.list_reminders()
