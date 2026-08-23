"""Tests for Ader's current SQLite database manager."""

import asyncio
import sqlite3
from pathlib import Path

from database.db_manager import DatabaseManager


def run(coro):
    return asyncio.run(coro)


def test_user_creation_and_retrieval(tmp_path: Path):
    async def scenario():
        db = DatabaseManager(str(tmp_path / "ader.sqlite3"))
        await db.connect()
        try:
            user = await db.create_user(123456789, 987654321)
            assert user["user_id"] == 123456789
            assert user["guild_id"] == 987654321
            assert user["balance"] == 0
            assert user["xp"] == 0
            assert user["level"] == 0

            retrieved = await db.get_user(123456789, 987654321)
            assert retrieved is not None
            assert retrieved["user_id"] == 123456789
        finally:
            await db.disconnect()

    run(scenario())


def test_balance_operations(tmp_path: Path):
    async def scenario():
        db = DatabaseManager(str(tmp_path / "ader.sqlite3"))
        await db.connect()
        try:
            await db.create_user(123, 456)
            assert await db.add_balance(123, 456, 500)
            assert await db.get_balance(123) == 500
            assert await db.remove_balance(123, 456, 300)
            assert await db.get_balance(123) == 200
            assert not await db.remove_balance(123, 456, 999)
        finally:
            await db.disconnect()

    run(scenario())


def test_guild_creation(tmp_path: Path):
    async def scenario():
        db = DatabaseManager(str(tmp_path / "ader.sqlite3"))
        await db.connect()
        try:
            guild = await db.create_guild(987654321)
            assert guild["guild_id"] == 987654321
            assert guild["modules"] == {}
        finally:
            await db.disconnect()

    run(scenario())


def test_leaderboard(tmp_path: Path):
    async def scenario():
        db = DatabaseManager(str(tmp_path / "ader.sqlite3"))
        await db.connect()
        try:
            for i in range(5):
                await db.create_user(100 + i, 987654321, {"xp": (i + 1) * 100})
            leaderboard = await db.get_leaderboard(987654321, limit=5)
            assert len(leaderboard) == 5
            assert leaderboard[0]["xp"] > leaderboard[-1]["xp"]
        finally:
            await db.disconnect()

    run(scenario())


def test_legacy_duplicate_users_are_merged_before_unique_index(tmp_path: Path):
    """A persistent pre-index database must upgrade without losing user data."""
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE users (
            user_id INTEGER NOT NULL, guild_id INTEGER NOT NULL, xp INTEGER NOT NULL DEFAULT 0,
            level INTEGER NOT NULL DEFAULT 0, balance INTEGER NOT NULL DEFAULT 0,
            inventory TEXT NOT NULL DEFAULT '[]', warnings TEXT NOT NULL DEFAULT '[]',
            created_at REAL NOT NULL
        )"""
    )
    connection.executemany(
        "INSERT INTO users VALUES(?,?,?,?,?,?,?,?)",
        [
            (12, 34, 10, 1, 20, '["first"]', '[]', 100.0),
            (12, 34, 15, 2, 30, '["second"]', '["warn"]', 200.0),
        ],
    )
    connection.commit()
    connection.close()

    async def scenario():
        db = DatabaseManager(str(path))
        await db.connect()
        try:
            users = await db.fetchall("SELECT * FROM users WHERE guild_id=? AND user_id=?", (34, 12))
            assert len(users) == 1
            user = users[0]
            assert user["xp"] == 25
            assert user["balance"] == 50
            assert user["level"] == 2
            assert user["created_at"] == 100.0
            assert not await db.fetchall("SELECT guild_id,user_id FROM users GROUP BY guild_id,user_id HAVING COUNT(*) > 1")
            indexes = await db.fetchall("PRAGMA index_list(users)")
            assert any(row["name"] == "idx_users_guild_user_unique" and row["unique"] for row in indexes)
        finally:
            await db.disconnect()

        # A second startup is idempotent and canonical creation cannot duplicate.
        db = DatabaseManager(str(path))
        await db.connect()
        try:
            await db.create_user(12, 34)
            assert len(await db.fetchall("SELECT * FROM users WHERE guild_id=? AND user_id=?", (34, 12))) == 1
        finally:
            await db.disconnect()

    run(scenario())
