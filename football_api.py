import asyncio
import time
from statistics import median

import httpx
from config import settings


class FootballAPI:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base = settings.football_api_base
        self.headers = {"x-apisports-key": api_key}
        self._cache = {}
        self.daily_limit = None
        self.daily_remaining = None
        self.minute_limit = None
        self.minute_remaining = None

    def _cached(self, key, ttl):
        item = self._cache.get(key)
        if item and time.time() - item[0] < ttl:
            return item[1]
        return None

    def _store(self, key, value):
        self._cache[key] = (time.time(), value)
        return value

    async def _get(self, path: str, params: dict | None = None) -> dict:
        timeout = httpx.Timeout(20.0)
        async with httpx.AsyncClient(timeout=timeout, headers=self.headers) as client:
            res = await client.get(f"{self.base}/{path.lstrip('/')}", params=params)
            self.daily_limit = res.headers.get("x-ratelimit-requests-limit", self.daily_limit)
            self.daily_remaining = res.headers.get("x-ratelimit-requests-remaining", self.daily_remaining)
            self.minute_limit = res.headers.get("x-ratelimit-limit", self.minute_limit)
            self.minute_remaining = res.headers.get("x-ratelimit-remaining", self.minute_remaining)
            res.raise_for_status()
            data = res.json()
            if data.get("errors"):
                raise RuntimeError(str(data["errors"]))
            return data

    async def ping(self):
        return await self._get("status")

    async def fixtures_by_date(self, date_str: str):
        key = ("fixtures-date", date_str)
        hit = self._cached(key, 300)
        if hit is not None:
            return hit
        data = await self._get("fixtures", {"date": date_str, "timezone": "Europe/Warsaw"})
        return self._store(key, data.get("response", []))

    async def fixture(self, fixture_id: int):
        key = ("fixture", fixture_id)
        hit = self._cached(key, 120)
        if hit is not None:
            return hit
        data = await self._get("fixtures", {"id": fixture_id})
        rows = data.get("response", [])
        return self._store(key, rows[0] if rows else None)

    async def fixture_statistics(self, fixture_id: int):
        data = await self._get("fixtures/statistics", {"fixture": fixture_id})
        return data.get("response", [])

    async def prediction(self, fixture_id: int):
        key = ("prediction", fixture_id)
        hit = self._cached(key, 1800)
        if hit is not None:
            return hit
        data = await self._get("predictions", {"fixture": fixture_id})
        rows = data.get("response", [])
        return self._store(key, rows[0] if rows else None)

    async def odds(self, fixture_id: int):
        key = ("odds", fixture_id)
        hit = self._cached(key, 1800)
        if hit is not None:
            return hit
        data = await self._get("odds", {"fixture": fixture_id})
        rows = data.get("response", [])
        return self._store(key, rows[0] if rows else None)

    async def prediction_and_odds(self, fixture_id: int):
        # Dwa wywołania na mecz. Robimy je sekwencyjnie, żeby łatwiej pilnować free limitu.
        pred = await self.prediction(fixture_id)
        odds = await self.odds(fixture_id)
        return pred, odds


async def odds_by_date(self, date_str: str, max_pages: int = 5):
    key = ("odds-date", date_str, max_pages)
    hit = self._cached(key, 900)
    if hit is not None:
        return hit
    rows = []
    page = 1
    while page <= max_pages:
        data = await self._get("odds", {"date": date_str, "page": page})
        rows.extend(data.get("response", []))
        paging = data.get("paging") or {}
        current = int(paging.get("current") or page)
        total = int(paging.get("total") or current)
        if current >= total:
            break
        page += 1
    return self._store(key, rows)

    @staticmethod
    def match_winner_odds(odds_payload: dict | None):
        if not odds_payload:
            return {}
        values = {"Home": [], "Draw": [], "Away": []}
        for bookmaker in odds_payload.get("bookmakers") or []:
            for bet in bookmaker.get("bets") or []:
                name = (bet.get("name") or "").strip().lower()
                if name not in {"match winner", "1x2"}:
                    continue
                for entry in bet.get("values") or []:
                    label = entry.get("value")
                    if label in values:
                        try:
                            odd = float(entry.get("odd"))
                            if 1.01 <= odd <= 50:
                                values[label].append(odd)
                        except (TypeError, ValueError):
                            pass
        return {k: round(median(v), 2) for k, v in values.items() if v}
