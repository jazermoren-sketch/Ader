import pytest

from cogs import owner_delegate_permissions as patch


class DummyRow:
    pass


class DummyDB:
    async def fetchone(self, query, params):
        return DummyRow() if int(params[0]) == 123 else None


class DummyCog:
    async def _resolve_member(self, ctx, value):
        return value

    async def _delegate(self, ctx, member):
        self.delegated = member

    async def _undelegate(self, ctx, member):
        self.undelegated = member


class DummyBot:
    def __init__(self):
        self.db = DummyDB()
        self._cog = DummyCog()

    def get_cog(self, name):
        return self._cog if name == "OwnerCurrency" else None


@pytest.mark.asyncio
async def test_delegated_owner_permission_reads_owner_delegate_table():
    bot = DummyBot()
    user = type("User", (), {"id": 123})()
    assert await patch._is_owner_with_delegates(bot, user) is True


@pytest.mark.asyncio
async def test_non_delegated_user_is_not_owner():
    bot = DummyBot()
    user = type("User", (), {"id": 456})()
    assert await patch._is_owner_with_delegates(bot, user) is False
