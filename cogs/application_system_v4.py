from __future__ import annotations
from .application_system_v3 import App, Questions

class AppV4(App):
    async def action(self, interaction, n, action):
        if action == 'count':
            p = await self.get(interaction.guild.id, n)
            current = len(p['questions'])
            target = {5: 7, 7: 10, 10: 5}.get(current, 5)
            defaults = __import__('cogs.application_system_v3', fromlist=['DEFAULT']).DEFAULT
            while len(p['questions']) < target:
                p['questions'].append(dict(defaults[len(p['questions']) % len(defaults)]))
            p['questions'] = p['questions'][:target]
            await self.save(interaction.guild.id, n, p)
            return await self.settings(interaction, n)
        return await super().action(interaction, n, action)

async def setup(bot):
    await bot.add_cog(AppV4(bot))
