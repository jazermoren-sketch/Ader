"""Interactive ANOCoin shop for Ader."""
from __future__ import annotations

import json
import time
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import EmbedFactory, EmbedColor


class ShopSelect(discord.ui.Select):
    def __init__(self, cog: "Shop", items: list[dict[str, Any]], user_id: int):
        self.cog = cog
        self.user_id = user_id
        options = []
        for item in items[:25]:
            data = self.cog._item_data(item)
            stock = data.get("stock", -1)
            stock_text = "∞" if stock < 0 else str(stock)
            options.append(
                discord.SelectOption(
                    label=str(item["name"])[:100],
                    description=f"{int(item['price']):,} ANOCoin • المخزون: {stock_text}"[:100],
                    value=str(item["id"]),
                    emoji="🛒",
                )
            )
        super().__init__(placeholder="اختر منتجاً لعرض تفاصيله وشرائه...", options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ هذه القائمة ليست لك.", ephemeral=True)
        item = await self.cog._get_item(interaction.guild.id, int(self.values[0]))
        if not item:
            return await interaction.response.send_message("❌ هذا المنتج لم يعد موجوداً.", ephemeral=True)
        await interaction.response.send_message(embed=self.cog._item_embed(item), view=PurchaseView(self.cog, item["id"], self.user_id), ephemeral=True)


class ShopView(discord.ui.View):
    def __init__(self, cog: "Shop", items: list[dict[str, Any]], user_id: int):
        super().__init__(timeout=180)
        self.add_item(ShopSelect(cog, items, user_id))


class PurchaseView(discord.ui.View):
    def __init__(self, cog: "Shop", item_id: int, user_id: int):
        super().__init__(timeout=120)
        self.cog = cog
        self.item_id = item_id
        self.user_id = user_id

    @discord.ui.button(label="شراء", emoji="🛒", style=discord.ButtonStyle.success)
    async def buy(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ هذه العملية ليست لك.", ephemeral=True)
        ok, message = await self.cog._purchase(interaction.guild.id, interaction.user.id, self.item_id)
        if ok:
            await interaction.response.edit_message(embed=EmbedFactory.success("تم الشراء", message), view=None)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @discord.ui.button(label="إلغاء", emoji="✖️", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ هذه العملية ليست لك.", ephemeral=True)
        await interaction.response.edit_message(content="❌ تم إلغاء عملية الشراء.", embed=None, view=None)


class Shop(commands.Cog):
    """Full ANOCoin shop: browsing, purchasing, inventory and administration."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = bot.db
        self.config = bot.config
        self.currency_name = self.config.get("modules", {}).get("economy", {}).get("currency_name", "ANOCoin")
        # /shop already existed in Economy. Remove it before registering the
        # complete implementation so there is exactly one application command.
        self.bot.tree.remove_command("shop")

    @staticmethod
    def _item_data(item: dict[str, Any]) -> dict[str, Any]:
        try:
            data = json.loads(item.get("data") or "{}")
            return data if isinstance(data, dict) else {}
        except (TypeError, json.JSONDecodeError):
            return {}

    async def _get_items(self, guild_id: int) -> list[dict[str, Any]]:
        rows = await self.db.fetchall("SELECT * FROM shop WHERE guild_id=? ORDER BY id ASC", (guild_id,))
        return [dict(row) for row in rows]

    async def _get_item(self, guild_id: int, item_id: int) -> dict[str, Any] | None:
        row = await self.db.fetchone("SELECT * FROM shop WHERE guild_id=? AND id=?", (guild_id, item_id))
        return dict(row) if row else None

    def _item_embed(self, item: dict[str, Any]) -> discord.Embed:
        data = self._item_data(item)
        stock = data.get("stock", -1)
        stock_text = "غير محدود" if stock < 0 else f"{stock:,}"
        description = str(data.get("description") or "بدون وصف.")
        return EmbedFactory.create(
            title=f"🛒 {item['name']}",
            description=(
                f"{description}\n\n"
                f"💰 السعر: **{int(item['price']):,} {self.currency_name}**\n"
                f"📦 المخزون: **{stock_text}**"
            ),
            color=EmbedColor.ECONOMY,
        )

    @app_commands.command(name="shop", description="Browse and buy items from the server ANOCoin shop")
    async def shop(self, interaction: discord.Interaction):
        items = await self._get_items(interaction.guild.id)
        if not items:
            return await interaction.response.send_message(
                embed=EmbedFactory.info("🏪 المتجر فارغ", "لم تتم إضافة أي منتجات للمتجر بعد."), ephemeral=True
            )
        balance = await self.db.get_balance(interaction.user.id)
        description = "\n".join(
            f"`#{item['id']}` **{item['name']}** — **{int(item['price']):,} {self.currency_name}**"
            for item in items[:25]
        )
        embed = EmbedFactory.create(
            title="🏪 ANOCoin Shop",
            description=f"رصيدك: **{balance:,} {self.currency_name}**\n\n{description}\n\nاختر منتجاً من القائمة لعرض التفاصيل والشراء.",
            color=EmbedColor.ECONOMY,
        )
        await interaction.response.send_message(embed=embed, view=ShopView(self, items, interaction.user.id))

    @app_commands.command(name="inventory", description="View your purchased shop items")
    async def inventory(self, interaction: discord.Interaction):
        user = await self.db.get_user(interaction.user.id, interaction.guild.id) or await self.db.create_user(interaction.user.id, interaction.guild.id)
        inventory = user.get("inventory", [])
        if not inventory:
            return await interaction.response.send_message(embed=EmbedFactory.info("🎒 Inventory", "مخزونك فارغ حالياً."), ephemeral=True)
        lines = []
        for index, item in enumerate(inventory, 1):
            if isinstance(item, dict):
                name = item.get("name", "Item")
                purchased = item.get("purchased_at")
                lines.append(f"**{index}. {name}**" + (f" — <t:{int(purchased)}:R>" if purchased else ""))
            else:
                lines.append(f"**{index}. {item}**")
        embed = EmbedFactory.create(title="🎒 Inventory", description="\n".join(lines[-25:]), color=EmbedColor.ECONOMY)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="shop-add", description="Add an item to the ANOCoin shop")
    @app_commands.describe(name="Item name", price="Price in ANOCoin", description="Item description", stock="Stock; use -1 for unlimited")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def shop_add(self, interaction: discord.Interaction, name: str, price: int, description: str, stock: int = -1):
        if price <= 0 or stock < -1:
            return await interaction.response.send_message("❌ السعر يجب أن يكون موجباً والمخزون -1 أو أكبر.", ephemeral=True)
        await self.db.execute(
            "INSERT INTO shop(guild_id,name,price,data) VALUES(?,?,?,?)",
            (interaction.guild.id, name[:100], price, json.dumps({"description": description[:1000], "stock": stock}, ensure_ascii=False)),
        )
        await interaction.response.send_message(embed=EmbedFactory.success("تمت إضافة المنتج", f"🛒 **{name}** — **{price:,} {self.currency_name}**"), ephemeral=True)

    @app_commands.command(name="shop-remove", description="Remove an item from the ANOCoin shop")
    @app_commands.describe(item_id="Shop item ID")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def shop_remove(self, interaction: discord.Interaction, item_id: int):
        cur = await self.db.execute("DELETE FROM shop WHERE guild_id=? AND id=?", (interaction.guild.id, item_id))
        if cur.rowcount == 0:
            return await interaction.response.send_message("❌ المنتج غير موجود.", ephemeral=True)
        await interaction.response.send_message(embed=EmbedFactory.success("تم حذف المنتج", f"تم حذف المنتج `#{item_id}`."), ephemeral=True)

    @app_commands.command(name="shop-edit", description="Edit an item in the ANOCoin shop")
    @app_commands.describe(item_id="Shop item ID", name="New name", price="New price", description="New description", stock="New stock; -1 for unlimited")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def shop_edit(self, interaction: discord.Interaction, item_id: int, name: str | None = None, price: int | None = None, description: str | None = None, stock: int | None = None):
        item = await self._get_item(interaction.guild.id, item_id)
        if not item:
            return await interaction.response.send_message("❌ المنتج غير موجود.", ephemeral=True)
        if price is not None and price <= 0:
            return await interaction.response.send_message("❌ السعر يجب أن يكون موجباً.", ephemeral=True)
        if stock is not None and stock < -1:
            return await interaction.response.send_message("❌ المخزون يجب أن يكون -1 أو أكبر.", ephemeral=True)
        data = self._item_data(item)
        if description is not None:
            data["description"] = description[:1000]
        if stock is not None:
            data["stock"] = stock
        new_name = name[:100] if name else item["name"]
        new_price = price if price is not None else item["price"]
        await self.db.execute("UPDATE shop SET name=?, price=?, data=? WHERE guild_id=? AND id=?", (new_name, new_price, json.dumps(data, ensure_ascii=False), interaction.guild.id, item_id))
        await interaction.response.send_message(embed=EmbedFactory.success("تم تعديل المنتج", f"تم تحديث المنتج `#{item_id}`."), ephemeral=True)

    async def _purchase(self, guild_id: int, user_id: int, item_id: int) -> tuple[bool, str]:
        await self.db.create_user(user_id, guild_id)
        conn = self.db.connection
        if conn is None:
            return False, "❌ قاعدة البيانات غير متصلة."
        try:
            await conn.execute("BEGIN IMMEDIATE")
            row = await (await conn.execute("SELECT * FROM shop WHERE guild_id=? AND id=?", (guild_id, item_id))).fetchone()
            if not row:
                await conn.rollback()
                return False, "❌ المنتج لم يعد موجوداً."
            item = dict(row)
            data = self._item_data(item)
            stock = int(data.get("stock", -1))
            price = int(item["price"])
            balance_row = await (await conn.execute("SELECT balance FROM global_balances WHERE user_id=?", (user_id,))).fetchone()
            balance = int(balance_row[0]) if balance_row else 0
            if balance < price:
                await conn.rollback()
                return False, f"❌ رصيدك غير كافٍ. تحتاج **{price:,} {self.currency_name}** ورصيدك الحالي **{balance:,} {self.currency_name}**."
            if stock == 0:
                await conn.rollback()
                return False, "❌ هذا المنتج نفد من المخزون."
            if stock > 0:
                data["stock"] = stock - 1
                cur = await conn.execute("UPDATE shop SET data=? WHERE id=? AND guild_id=?", (json.dumps(data, ensure_ascii=False), item_id, guild_id))
                if cur.rowcount != 1:
                    await conn.rollback()
                    return False, "❌ تعذر تحديث المخزون. حاول مرة أخرى."
            cur = await conn.execute("UPDATE global_balances SET balance=balance-? WHERE user_id=? AND balance>=?", (price, user_id, price))
            if cur.rowcount != 1:
                await conn.rollback()
                return False, "❌ تغير رصيدك قبل إتمام الشراء. حاول مرة أخرى."
            user_row = await (await conn.execute("SELECT inventory FROM users WHERE user_id=? AND guild_id=?", (user_id, guild_id))).fetchone()
            inventory = json.loads(user_row[0] or "[]") if user_row else []
            inventory.append({"shop_item_id": item_id, "name": item["name"], "price": price, "purchased_at": int(time.time())})
            await conn.execute("UPDATE users SET inventory=? WHERE user_id=? AND guild_id=?", (json.dumps(inventory, ensure_ascii=False), user_id, guild_id))
            await conn.commit()
            # Product delivery is deliberately a supported extension point, not a
            # replacement of this method by an advertising hotfix.
            delivery = getattr(self.bot, "ad_delivery_handler", None)
            if delivery is not None:
                delivered, delivery_text = await delivery(guild_id, user_id, item)
                if not delivered:
                    # The delivery handler refunds its own failed delivery.  The
                    # purchase remains recorded for auditability.
                    return False, delivery_text
            new_balance = balance - price
            stock_text = "غير محدود" if stock < 0 else str(stock - 1)
            text = f"اشتريت **{item['name']}** مقابل **{price:,} {self.currency_name}**.\nرصيدك الجديد: **{new_balance:,} {self.currency_name}**.\nالمخزون المتبقي: **{stock_text}**."
            return True, text + (f"\n{delivery_text}" if delivery is not None and delivery_text else "")
        except Exception:
            try:
                await conn.rollback()
            except Exception:
                pass
            return False, "❌ وقع خطأ أثناء الشراء. لم يتم خصم أي رصيد."


async def setup(bot: commands.Bot):
    await bot.add_cog(Shop(bot))
