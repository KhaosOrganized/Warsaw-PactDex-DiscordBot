import logging

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button, View, button
from tortoise.exceptions import DoesNotExist

from typing import TYPE_CHECKING, Optional, cast

from ballsdex.core.models import BallInstance
from ballsdex.core.models import Player
from ballsdex.core.models import specials
from ballsdex.core.models import balls
from ballsdex.core.utils.transformers import BallEnabledTransform
from ballsdex.core.utils.transformers import BallTransform
from ballsdex.core.utils.transformers import SpecialEnabledTransform
from ballsdex.core.utils.transformers import SpecialTransform
from ballsdex.core.utils.buttons import ConfirmChoiceView
from ballsdex.core.utils.paginator import FieldPageSource, Pages
from ballsdex.core.utils.sorting import SortingChoices, sort_balls
from ballsdex.settings import settings
from ballsdex.core.utils.logging import log_action

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

# You must have a special called "Collector" and "Diamond" for this to work.
# Emerald is also supported here.

T1Req = 65
T1Rarity = 0.025
CommonReq = 890
CommonRarity = 1.0
RoundingOption = 10

dT1Req = 2
dT1Rarity = 0.025
dCommonReq = 10
dCommonRarity = 1.0
dRoundingOption = 1

log = logging.getLogger("ballsdex.packages.collector.cog")

gradient = (CommonReq - T1Req) / (CommonRarity - T1Rarity)
dgradient = (dCommonReq - dT1Req) / (dCommonRarity - dT1Rarity)


class Collector(commands.GroupCog):
    """
    Collector commands.
    """

    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot

    ccadmin = app_commands.Group(name="admin", description="admin commands for collector")

    @app_commands.command()
    async def card(
        self,
        interaction: discord.Interaction,
        countryball: BallEnabledTransform,
        diamond: bool | None = False,
        emerald: bool | None = False,
    ):
        """
        Get the collector/diamond/emerald card for a countryball.
        """
        if interaction.response.is_done():
            return
        assert interaction.guild

        await interaction.response.defer(ephemeral=True, thinking=True)

        # Select special type
        if emerald:
            special = [x for x in specials.values() if x.name == "Emerald"][0]
        elif diamond:
            special = [x for x in specials.values() if x.name == "Diamond"][0]
        else:
            special = [x for x in specials.values() if x.name == "Collector"][0]

        # Already has check
        checkfilter = {
            "special": special,
            "player__discord_id": interaction.user.id,
            "ball": countryball,
        }
        if await BallInstance.filter(**checkfilter).count() >= 1:
            return await interaction.followup.send(
                f"You already have a {countryball.country} {special.name} card."
            )

        # Emerald logic
        if emerald:
            # Always require Collector and Diamond, even if they don't exist
            static_required_names = ["Collector", "Diamond"]

            # Also include any existing specials for this ball (except Shiny/Emerald)
            existing_special_ids = await BallInstance.filter(ball=countryball).values_list("special_id", flat=True)
            dynamic_required_specials = [
                s for s in specials.values()
                if s.id in existing_special_ids and s.name not in ["Shiny", "Emerald"]
            ]

            # Combine the static and dynamic lists, avoiding duplicates
            required_specials = []
            seen = set()
            for s in specials.values():
                if s.name in static_required_names or s in dynamic_required_specials:
                    if s.name not in ["Shiny", "Emerald"] and s.name not in seen:
                        required_specials.append(s)
                        seen.add(s.name)

            # Check if the player owns all required specials
            missing = []
            for req in required_specials:
                has = await BallInstance.filter(
                    ball=countryball,
                    player__discord_id=interaction.user.id,
                    special=req,
                ).exists()
                if not has:
                    missing.append(req.name)

            if missing:
                return await interaction.followup.send(
                    f"You are missing the following specials for {countryball.country}: {', '.join(missing)}"
                )

            player, _ = await Player.get_or_create(discord_id=interaction.user.id)
            await BallInstance.create(
                ball=countryball, player=player, attack_bonus=0, health_bonus=0, special=special
            )
            return await interaction.followup.send(
                f"Congrats! You created an **Emerald {countryball.country}** card!"
            )


        # Collector/Diamond logic
        filters = {"ball": countryball, "player__discord_id": interaction.user.id}
        if diamond:
            shiny = [x for x in specials.values() if x.name == "Shiny"][0]
            filters["special"] = shiny
        balls_count = await BallInstance.filter(**filters).count()

        if diamond:
            collector_number = int(
                int((dgradient * (countryball.rarity - dT1Rarity) + dT1Req) / dRoundingOption)
                * dRoundingOption
            )
        else:
            collector_number = int(
                int((gradient * (countryball.rarity - T1Rarity) + T1Req) / RoundingOption)
                * RoundingOption
            )

        player, _ = await Player.get_or_create(discord_id=interaction.user.id)
        if balls_count >= collector_number:
            await interaction.followup.send(
                f"Congrats! You are now a {countryball.country} {special.name} collector.",
                ephemeral=True,
            )
            await BallInstance.create(
                ball=countryball, player=player, attack_bonus=0, health_bonus=0, special=special
            )
        else:
            shinytext = " Shiny✨" if diamond else ""
            await interaction.followup.send(
                f"You need {collector_number}{shinytext} {countryball.country} to create a {special.name} card. You currently have {balls_count}"
            )

    @app_commands.command()
    async def list(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        diamond: bool | None = False,
        emerald: bool | None = False,
    ):
        """
        Show the collector/diamond/emerald card list of the dex.
        """
        enabled_collectibles = [x for x in balls.values() if x.enabled]
        if not enabled_collectibles:
            await interaction.response.send_message(
                f"There are no collectibles registered in {settings.bot_name} yet.",
                ephemeral=True,
            )
            return

        sorted_collectibles = sorted(enabled_collectibles, key=lambda x: x.rarity)
        entries = []

        if emerald:
            text0 = "Emerald"
            reqtext = "Must own all specials (except Shiny/Emerald)"
        elif diamond:
            text0 = "Diamond"
            reqtext = "Shinies✨ required"
        else:
            text0 = "Collector"
            reqtext = "Amount required"

        for collectible in sorted_collectibles:
            emoji = self.bot.get_emoji(collectible.emoji_id)
            emote = str(emoji) if emoji else "N/A"

            if emerald:
                entry = (collectible.country, f"{emote}{reqtext}")
            elif diamond:
                rarity1 = int(
                    int((dgradient * (collectible.rarity - dT1Rarity) + dT1Req) / dRoundingOption)
                    * dRoundingOption
                )
                entry = (collectible.country, f"{emote}{reqtext}: {rarity1}")
            else:
                rarity1 = int(
                    int((gradient * (collectible.rarity - T1Rarity) + T1Req) / RoundingOption)
                    * RoundingOption
                )
                entry = (collectible.country, f"{emote}{reqtext}: {rarity1}")

            entries.append(entry)

        source = FieldPageSource(entries, per_page=5, inline=False, clear_description=False)
        source.embed.description = f"__**{settings.bot_name} {text0} Card List**__"
        source.embed.colour = (
            discord.Colour.from_rgb(0, 255, 127) if emerald else discord.Colour.from_rgb(190, 100, 190)
        )
        source.embed.set_author(
            name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url
        )

        pages = Pages(source=source, interaction=interaction, compact=True)
        await pages.start(ephemeral=True)

    @ccadmin.command(name="check")
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    @app_commands.choices(
        option=[
            app_commands.Choice(name="Show all CCs", value="ALL"),
            app_commands.Choice(name="Show only unmet CCs", value="UNMET"),
            app_commands.Choice(name="Delete all unmet CCs", value="DELETE"),
        ]
    )
    async def check(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        option: str,
        countryball: BallTransform | None = None,
        user: discord.User | None = None,
        diamond: bool | None = False,
        emerald: bool | None = False,
    ):
        """
        Check for unmet Collector/Diamond/Emerald Cards
        """
        if option == "DELETE":
            fullperm = any(
                interaction.guild.get_role(i) in interaction.user.roles
                for i in settings.root_role_ids
            )
            if not fullperm:
                return await interaction.response.send_message(
                    f"You do not have permission to delete {settings.plural_collectible_name}",
                    ephemeral=True,
                )

        await interaction.response.defer(ephemeral=True, thinking=True)

        if emerald:
            collectorspecial = [x for x in specials.values() if x.name == "Emerald"][0]
        elif diamond:
            collectorspecial = [x for x in specials.values() if x.name == "Diamond"][0]
        else:
            collectorspecial = [x for x in specials.values() if x.name == "Collector"][0]

        filters = {"special": collectorspecial}
        if countryball:
            filters["ball"] = countryball
        if user:
            filters["player__discord_id"] = user.id

        entries = []
        unmetlist = []

        balls_qs = await BallInstance.filter(**filters).prefetch_related("player", "special", "ball")
        for ball in balls_qs:
            player = await self.bot.fetch_user(int(f"{ball.player}"))
            checkfilter = {"player__discord_id": int(f"{ball.player}"), "ball": ball.ball}

            if emerald:
                # Gather only specials that exist for this ball (except Shiny/Emerald)
                existing_special_ids = await BallInstance.filter(ball=ball.ball).values_list("special_id", flat=True)
                required_specials = [
                    s for s in specials.values()
                    if s.id in existing_special_ids and s.name not in ["Shiny", "Emerald"]
                ]

                missing = []
                for req in required_specials:
                    has = await BallInstance.filter(
                        ball=ball.ball, player__discord_id=int(f"{ball.player}"), special=req
                    ).exists()
                    if not has:
                        missing.append(req.name)

                if not missing:
                    if option == "ALL":
                        entries.append(
                            (
                                ball.description(short=True, include_emoji=True, bot=self.bot),
                                f"{player}({ball.player})\nAll requirements met ✅",
                            )
                        )
                else:
                    entries.append(
                        (
                            ball.description(short=True, include_emoji=True, bot=self.bot),
                            f"{player}({ball.player})\nMissing: {', '.join(missing)} ⚠️",
                        )
                    )
                    unmetlist.append(ball)

            elif diamond:
                checkfilter["special"] = [x for x in specials.values() if x.name == "Shiny"][0]
                shinycount = await BallInstance.filter(**checkfilter).count()
                rarity_req = int(
                    int((dgradient * (ball.ball.rarity - dT1Rarity) + dT1Req) / dRoundingOption)
                    * dRoundingOption
                )
                if shinycount >= rarity_req:
                    if option == "ALL":
                        entries.append(
                            (
                                ball.description(short=True, include_emoji=True, bot=self.bot),
                                f"{player}({ball.player})\n{shinycount} shinies ✅",
                            )
                        )
                else:
                    entries.append(
                        (
                            ball.description(short=True, include_emoji=True, bot=self.bot),
                            f"{player}({ball.player})\n{shinycount} shinies ⚠️",
                        )
                    )
                    unmetlist.append(ball)

            else:
                count = await BallInstance.filter(**checkfilter).count()
                rarity_req = int(
                    int((gradient * (ball.ball.rarity - T1Rarity) + T1Req) / RoundingOption)
                    * RoundingOption
                )
                if count >= rarity_req:
                    if option == "ALL":
                        entries.append(
                            (
                                ball.description(short=True, include_emoji=True, bot=self.bot),
                                f"{player}({ball.player})\n{count} owned ✅",
                            )
                        )
                else:
                    entries.append(
                        (
                            ball.description(short=True, include_emoji=True, bot=self.bot),
                            f"{player}({ball.player})\n{count} owned ⚠️",
                        )
                    )
                    unmetlist.append(ball)

        text0 = "emerald" if emerald else "diamond" if diamond else "collector"

        if not entries:
            return await interaction.followup.send(f"No {text0} cards found for this filter!")

        if option == "DELETE" and unmetlist:
            unmetballs = "\n".join(
                [f"{await self.bot.fetch_user(int(f'{b.player}'))}'s {b}" for b in unmetlist]
            )
            with open("unmetccs.txt", "w") as file:
                file.write(unmetballs)
            with open("unmetccs.txt", "rb") as file:
                await interaction.followup.send(
                    f"The following {text0} cards will be deleted for not meeting requirements:",
                    file=discord.File(file, "unmetccs.txt"),
                    ephemeral=True,
                )
            view = ConfirmChoiceView(
                interaction,
                accept_message=f"Confirmed, deleting...",
                cancel_message="Request cancelled.",
            )
            await interaction.followup.send(
                f"Are you sure you want to delete {len(unmetlist)} {text0} card(s)?\nThis cannot be undone.",
                view=view,
                ephemeral=True,
            )
            await view.wait()
            if not view.value:
                return
            for b in unmetlist:
                try:
                    userobj = await self.bot.fetch_user(int(f"{b.player}"))
                    await userobj.send(
                        f"Your {b.ball} {text0} card was deleted (requirements no longer met)."
                    )
                except:
                    pass
                await b.delete()
            await interaction.followup.send(f"{len(unmetlist)} {text0} cards deleted.", ephemeral=True)
            await log_action(
                f"{interaction.user} deleted {len(unmetlist)} {text0} cards for unmet requirements.",
                self.bot,
            )
            return

        # Paginate results
        source = FieldPageSource(entries, per_page=5, inline=False, clear_description=False)
        source.embed.description = f"__**{settings.bot_name} {text0.capitalize()} Card Check**__"
        source.embed.colour = (
            discord.Colour.from_rgb(0, 255, 127) if emerald else discord.Colour.from_rgb(190, 100, 190)
        )
        pages = Pages(source=source, interaction=interaction, compact=True)
        await pages.start(ephemeral=True)
