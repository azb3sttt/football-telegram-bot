import aiosqlite
from datetime import datetime, timezone


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    created_at TEXT NOT NULL,
    notify_selection INTEGER NOT NULL DEFAULT 1,
    notify_coupon INTEGER NOT NULL DEFAULT 1,
    notify_daily INTEGER NOT NULL DEFAULT 1,
    notify_weekly INTEGER NOT NULL DEFAULT 1,
    notify_monthly INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS coupons (
    coupon_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    combined_odds REAL,
    status TEXT NOT NULL DEFAULT 'pending',
    final_result TEXT,
    profit_loss REAL,
    is_placed INTEGER NOT NULL DEFAULT 0,
    stake_units REAL NOT NULL DEFAULT 1.0,
    placed_at TEXT,
    FOREIGN KEY(user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS selections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    coupon_id INTEGER,
    user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    fixture_id INTEGER NOT NULL,
    league TEXT,
    home_team TEXT,
    away_team TEXT,
    market TEXT NOT NULL,
    selection TEXT NOT NULL,
    line REAL,
    odds REAL NOT NULL,
    model_probability REAL,
    confidence REAL,
    edge REAL,
    status TEXT NOT NULL DEFAULT 'pending',
    final_result TEXT,
    profit_loss REAL,
    settled_at TEXT,
    FOREIGN KEY(coupon_id) REFERENCES coupons(coupon_id),
    FOREIGN KEY(user_id) REFERENCES users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_selections_status ON selections(status);
CREATE INDEX IF NOT EXISTS idx_selections_fixture ON selections(fixture_id);
CREATE INDEX IF NOT EXISTS idx_selections_created ON selections(created_at);
CREATE INDEX IF NOT EXISTS idx_coupons_user ON coupons(user_id);
CREATE INDEX IF NOT EXISTS idx_coupons_placed ON coupons(is_placed);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: str):
        self.path = path

    async def connect(self):
        db = await aiosqlite.connect(self.path)
        db.row_factory = aiosqlite.Row
        return db

    async def _ensure_column(self, db, table: str, column: str, ddl: str):
        cur = await db.execute(f"PRAGMA table_info({table})")
        cols = {r["name"] for r in await cur.fetchall()}
        if column not in cols:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    async def initialize(self):
        import os
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        db = await self.connect()
        try:
            await db.executescript(SCHEMA)
            # Migracja istniejącej bazy z V2 bez utraty historii.
            await self._ensure_column(db, "coupons", "is_placed", "INTEGER NOT NULL DEFAULT 0")
            await self._ensure_column(db, "coupons", "stake_units", "REAL NOT NULL DEFAULT 1.0")
            await self._ensure_column(db, "coupons", "placed_at", "TEXT")
            await db.commit()
        finally:
            await db.close()

    async def ensure_user(self, user_id: int, username: str | None):
        db = await self.connect()
        try:
            await db.execute(
                """INSERT INTO users(user_id, username, created_at)
                   VALUES(?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET username=excluded.username""",
                (user_id, username, utcnow()),
            )
            await db.commit()
        finally:
            await db.close()

    async def get_notification_settings(self, user_id: int):
        db = await self.connect()
        try:
            cur = await db.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
            return await cur.fetchone()
        finally:
            await db.close()

    async def toggle_notification(self, user_id: int, column: str):
        allowed = {
            "notify_selection", "notify_coupon", "notify_daily",
            "notify_weekly", "notify_monthly"
        }
        if column not in allowed:
            raise ValueError("Nieprawidłowe ustawienie")
        db = await self.connect()
        try:
            await db.execute(
                f"UPDATE users SET {column} = CASE {column} WHEN 1 THEN 0 ELSE 1 END WHERE user_id=?",
                (user_id,),
            )
            await db.commit()
        finally:
            await db.close()

    async def create_coupon(self, user_id: int, picks: list[dict]):
        combined = 1.0
        for pick in picks:
            combined *= float(pick["odds"])
        db = await self.connect()
        try:
            cur = await db.execute(
                """INSERT INTO coupons(
                    user_id, created_at, combined_odds, status, is_placed, stake_units
                ) VALUES(?, ?, ?, 'pending', 0, 1.0)""",
                (user_id, utcnow(), combined),
            )
            coupon_id = cur.lastrowid
            for pick in picks:
                await db.execute(
                    """INSERT INTO selections(
                        coupon_id,user_id,created_at,fixture_id,league,home_team,away_team,
                        market,selection,line,odds,model_probability,confidence,edge,status
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'pending')""",
                    (
                        coupon_id, user_id, utcnow(), pick["fixture_id"], pick.get("league"),
                        pick.get("home"), pick.get("away"), pick.get("market", "1X2"),
                        pick.get("raw_selection") or pick.get("selection"), pick.get("line"),
                        float(pick["odds"]), float(pick.get("probability") or 0),
                        float(pick.get("confidence") or 0), float(pick.get("edge") or 0)
                    ),
                )
            await db.commit()
            return coupon_id, combined
        finally:
            await db.close()

    async def get_coupon(self, user_id: int, coupon_id: int):
        db = await self.connect()
        try:
            cur = await db.execute(
                "SELECT * FROM coupons WHERE coupon_id=? AND user_id=?",
                (coupon_id, user_id),
            )
            coupon = await cur.fetchone()
            if not coupon:
                return None, []
            cur = await db.execute(
                "SELECT * FROM selections WHERE coupon_id=? ORDER BY id",
                (coupon_id,),
            )
            return coupon, await cur.fetchall()
        finally:
            await db.close()

    async def mark_coupon_placed(self, user_id: int, coupon_id: int, stake_units: float = 1.0):
        db = await self.connect()
        try:
            cur = await db.execute(
                "SELECT * FROM coupons WHERE coupon_id=? AND user_id=?",
                (coupon_id, user_id),
            )
            row = await cur.fetchone()
            if not row:
                return False, "not_found"
            if row["status"] != "pending":
                return False, "finished"
            if int(row["is_placed"] or 0) == 1:
                await db.execute(
                    "UPDATE coupons SET stake_units=? WHERE coupon_id=? AND user_id=?",
                    (stake_units, coupon_id, user_id),
                )
                await db.commit()
                return True, "updated"
            await db.execute(
                """UPDATE coupons
                   SET is_placed=1, stake_units=?, placed_at=?
                   WHERE coupon_id=? AND user_id=?""",
                (stake_units, utcnow(), coupon_id, user_id),
            )
            await db.commit()
            return True, "placed"
        finally:
            await db.close()

    async def unmark_coupon(self, user_id: int, coupon_id: int):
        db = await self.connect()
        try:
            cur = await db.execute(
                "SELECT * FROM coupons WHERE coupon_id=? AND user_id=?",
                (coupon_id, user_id),
            )
            row = await cur.fetchone()
            if not row:
                return False, "not_found"
            if row["status"] != "pending":
                return False, "finished"
            await db.execute(
                """UPDATE coupons
                   SET is_placed=0, stake_units=1.0, placed_at=NULL
                   WHERE coupon_id=? AND user_id=?""",
                (coupon_id, user_id),
            )
            await db.commit()
            return True, "ok"
        finally:
            await db.close()

    async def list_coupons(self, user_id: int, placed: int | None = None, pending_only=False, limit=12):
        db = await self.connect()
        try:
            where = ["user_id=?"]
            params = [user_id]
            if placed is not None:
                where.append("is_placed=?")
                params.append(int(placed))
            if pending_only:
                where.append("status='pending'")
            params.append(limit)
            cur = await db.execute(
                f"""SELECT * FROM coupons
                    WHERE {' AND '.join(where)}
                    ORDER BY coupon_id DESC LIMIT ?""",
                tuple(params),
            )
            return await cur.fetchall()
        finally:
            await db.close()

    async def pending_selections(self):
        db = await self.connect()
        try:
            cur = await db.execute(
                "SELECT * FROM selections WHERE status='pending' ORDER BY fixture_id"
            )
            return await cur.fetchall()
        finally:
            await db.close()

    async def fixture_selections(self, fixture_id: int):
        db = await self.connect()
        try:
            cur = await db.execute(
                "SELECT * FROM selections WHERE fixture_id=? AND status='pending'",
                (fixture_id,),
            )
            return await cur.fetchall()
        finally:
            await db.close()

    async def settle_selection(self, selection_id: int, status: str, final_result: str, profit_loss: float):
        db = await self.connect()
        try:
            await db.execute(
                """UPDATE selections
                   SET status=?, final_result=?, profit_loss=?, settled_at=?
                   WHERE id=?""",
                (status, final_result, profit_loss, utcnow(), selection_id),
            )
            await db.commit()
        finally:
            await db.close()

    async def refresh_coupon(self, coupon_id: int):
        db = await self.connect()
        try:
            cur = await db.execute("SELECT * FROM coupons WHERE coupon_id=?", (coupon_id,))
            coupon = await cur.fetchone()
            if not coupon:
                return None
            cur = await db.execute(
                "SELECT * FROM selections WHERE coupon_id=? ORDER BY id", (coupon_id,)
            )
            rows = await cur.fetchall()
            statuses = [r["status"] for r in rows]
            if not rows or any(s == "pending" for s in statuses):
                status, result, pnl = "pending", None, None
            elif any(s == "lost" for s in statuses):
                status, result = "lost", "Kupon nietrafiony"
                pnl = -float(coupon["stake_units"] or 1.0) if coupon["is_placed"] else 0.0
            else:
                won_rows = [r for r in rows if r["status"] == "won"]
                if not won_rows:
                    status, result, pnl = "push", "Zwrot kuponu", 0.0
                else:
                    effective_odds = 1.0
                    for r in won_rows:
                        effective_odds *= float(r["odds"])
                    status, result = "won", f"Kupon trafiony @ {effective_odds:.2f}"
                    stake = float(coupon["stake_units"] or 1.0)
                    pnl = stake * (effective_odds - 1.0) if coupon["is_placed"] else 0.0
            await db.execute(
                "UPDATE coupons SET status=?, final_result=?, profit_loss=? WHERE coupon_id=?",
                (status, result, pnl, coupon_id),
            )
            await db.commit()
            cur = await db.execute("SELECT * FROM coupons WHERE coupon_id=?", (coupon_id,))
            return await cur.fetchone()
        finally:
            await db.close()

    async def last_settled(self, user_id: int, limit: int = 10, placed_only=True):
        db = await self.connect()
        try:
            extra = "AND c.is_placed=1" if placed_only else ""
            cur = await db.execute(
                f"""SELECT s.*, c.is_placed, c.stake_units
                    FROM selections s
                    JOIN coupons c ON c.coupon_id=s.coupon_id
                    WHERE s.user_id=? AND s.status IN ('won','lost','push','void')
                    {extra}
                    ORDER BY COALESCE(s.settled_at, s.created_at) DESC LIMIT ?""",
                (user_id, limit),
            )
            return await cur.fetchall()
        finally:
            await db.close()

    async def report_rows(self, user_id: int, since_iso: str | None = None, placed_only=True):
        db = await self.connect()
        try:
            where = [
                "s.user_id=?",
                "s.status IN ('won','lost','push','void')",
            ]
            params = [user_id]
            if placed_only:
                where.append("c.is_placed=1")
            if since_iso:
                where.append("COALESCE(c.placed_at, s.created_at) >= ?")
                params.append(since_iso)
            cur = await db.execute(
                f"""SELECT s.*, c.is_placed, c.stake_units, c.placed_at
                    FROM selections s
                    JOIN coupons c ON c.coupon_id=s.coupon_id
                    WHERE {' AND '.join(where)}""",
                tuple(params),
            )
            return await cur.fetchall()
        finally:
            await db.close()

    async def coupon_report_rows(self, user_id: int, since_iso: str | None = None):
        db = await self.connect()
        try:
            where = ["user_id=?", "is_placed=1", "status IN ('won','lost','push','void')"]
            params = [user_id]
            if since_iso:
                where.append("placed_at >= ?")
                params.append(since_iso)
            cur = await db.execute(
                f"SELECT * FROM coupons WHERE {' AND '.join(where)} ORDER BY coupon_id DESC",
                tuple(params),
            )
            return await cur.fetchall()
        finally:
            await db.close()

    async def count_today_analyzed(self):
        db = await self.connect()
        try:
            cur = await db.execute(
                "SELECT COUNT(*) AS c FROM selections WHERE date(created_at)=date('now')"
            )
            row = await cur.fetchone()
            return int(row["c"])
        finally:
            await db.close()
