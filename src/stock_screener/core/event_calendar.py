"""
Manual event calendar loader for strategy skip days.
"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

import pandas as pd


@dataclass(frozen=True)
class CalendarEvent:
    date: pd.Timestamp
    ticker: str
    event_type: str
    time: Optional[str] = None
    description: Optional[str] = None
    source: Optional[str] = None


class EventCalendar:
    def __init__(self, path: str):
        self.path = path
        self._loaded = False
        self._events_by_date: Dict[pd.Timestamp, List[CalendarEvent]] = {}

    def _parse_date(self, value: str) -> Optional[pd.Timestamp]:
        if not value:
            return None
        try:
            return pd.Timestamp(value).normalize()
        except Exception:
            return None

    def _normalize_ticker(self, value: Optional[str]) -> str:
        if not value:
            return "ALL"
        ticker = value.strip().upper()
        return ticker if ticker else "ALL"

    def _normalize_event_type(self, value: Optional[str]) -> str:
        if not value:
            return "macro"
        event_type = value.strip().lower()
        return event_type if event_type else "macro"

    def load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.path or not os.path.exists(self.path):
            return
        with open(self.path, newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                raw_date = row.get("date") or row.get("Date")
                date_val = self._parse_date(raw_date)
                if date_val is None:
                    continue
                ticker = self._normalize_ticker(
                    row.get("ticker") or row.get("symbol") or row.get("Symbol")
                )
                event_type = self._normalize_event_type(
                    row.get("event_type") or row.get("type") or row.get("event")
                )
                time_val = (row.get("time") or row.get("Time") or "").strip() or None
                description = (row.get("description") or row.get("Description") or "").strip() or None
                source = (row.get("source") or row.get("Source") or "").strip() or None
                event = CalendarEvent(
                    date=date_val,
                    ticker=ticker,
                    event_type=event_type,
                    time=time_val,
                    description=description,
                    source=source,
                )
                self._events_by_date.setdefault(date_val, []).append(event)

    def should_skip(
        self,
        date: pd.Timestamp,
        ticker: Optional[str] = None,
        event_types: Optional[List[str]] = None,
    ) -> bool:
        self.load()
        if date is None:
            return False
        date_val = pd.Timestamp(date).normalize()
        events = self._events_by_date.get(date_val)
        if not events:
            return False
        ticker_val = ticker.strip().upper() if ticker else None
        allowed_types = None
        if event_types:
            allowed_types = {t.strip().lower() for t in event_types if t}
        for event in events:
            if allowed_types is not None and event.event_type not in allowed_types:
                continue
            if event.ticker == "ALL":
                return True
            if ticker_val and event.ticker == ticker_val:
                return True
        return False
