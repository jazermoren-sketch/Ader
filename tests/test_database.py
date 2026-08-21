"""Tests for Ader's current SQLite database manager."""

import asyncio
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
