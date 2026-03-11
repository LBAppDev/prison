import json
import os
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv


load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise SystemExit("Missing DISCORD_TOKEN in environment.")


DATA_DIR = Path(__file__).parent / "data"
CONFIG_PATH = DATA_DIR / "config.json"


def ensure_data_file() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps({"guilds": {}}, indent=2), encoding="utf-8")


def load_config() -> dict:
    ensure_data_file()
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def save_config(config: dict) -> None:
    ensure_data_file()
    CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")


def get_guild_config(config: dict, guild_id: int) -> dict:
    guild_key = str(guild_id)
    if guild_key not in config["guilds"]:
        config["guilds"][guild_key] = {"prisoners": {}}
    if "prisoners" not in config["guilds"][guild_key]:
        config["guilds"][guild_key]["prisoners"] = {}
    return config["guilds"][guild_key]


def is_admin(member: discord.Member) -> bool:
    return member.guild_permissions.administrator or member.id == member.guild.owner_id


def is_guard_or_admin(member: discord.Member, guild_config: dict) -> bool:
    if is_admin(member):
        return True
    guard_role_id = guild_config.get("guardRoleId")
    if not guard_role_id:
        return False
    return any(role.id == guard_role_id for role in member.roles)


def ensure_bot_permissions(guild: discord.Guild) -> str | None:
    bot_member = guild.me
    if not bot_member:
        return "Bot not in guild."
    perms = bot_member.guild_permissions
    if not perms.manage_roles or not perms.manage_channels:
        return "Bot needs Manage Roles and Manage Channels permissions."
    return None


intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


async def register_commands() -> None:
    await bot.tree.sync()
    print("Registered global commands")


async def ensure_setup(guild: discord.Guild, config: dict) -> dict:
    guild_config = get_guild_config(config, guild.id)

    prison_role = guild.get_role(guild_config.get("prisonRoleId", 0))
    if not prison_role:
        prison_role = discord.utils.get(guild.roles, name="Prisoner")
    if not prison_role:
        prison_role = await guild.create_role(name="Prisoner", reason="Prison bot setup")

    guard_role = guild.get_role(guild_config.get("guardRoleId", 0))
    if not guard_role:
        guard_role = discord.utils.get(guild.roles, name="Prison Guard")
    if not guard_role:
        guard_role = await guild.create_role(name="Prison Guard", reason="Prison bot setup")

    category = guild.get_channel(guild_config.get("prisonCategoryId", 0))
    if not category:
        category = discord.utils.get(guild.categories, name="Prison")
    if not category:
        category = await guild.create_category("Prison", reason="Prison bot setup")

    category_overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        prison_role: discord.PermissionOverwrite(view_channel=True),
        guard_role: discord.PermissionOverwrite(view_channel=True),
    }
    await category.edit(overwrites=category_overwrites, reason="Prison bot setup")

    prison_text = guild.get_channel(guild_config.get("prisonTextChannelId", 0))
    if not prison_text:
        prison_text = discord.utils.get(guild.text_channels, name="prison-chat")
    if not prison_text:
        prison_text = await guild.create_text_channel(
            "prison-chat", category=category, reason="Prison bot setup"
        )
    elif prison_text.category_id != category.id:
        await prison_text.edit(category=category, reason="Prison bot setup")

    prison_text_overwrites = dict(category_overwrites)
    prison_text_overwrites[prison_role] = discord.PermissionOverwrite(
        view_channel=True, send_messages=True, read_message_history=True
    )
    prison_text_overwrites[guard_role] = discord.PermissionOverwrite(
        view_channel=True, send_messages=True, read_message_history=True
    )
    await prison_text.edit(overwrites=prison_text_overwrites, reason="Prison bot setup")

    prison_voice = guild.get_channel(guild_config.get("prisonVoiceChannelId", 0))
    if not prison_voice:
        prison_voice = discord.utils.get(guild.voice_channels, name="prison-voice")
    if not prison_voice:
        prison_voice = await guild.create_voice_channel(
            "prison-voice", category=category, reason="Prison bot setup"
        )
    elif prison_voice.category_id != category.id:
        await prison_voice.edit(category=category, reason="Prison bot setup")

    prison_voice_overwrites = dict(category_overwrites)
    prison_voice_overwrites[prison_role] = discord.PermissionOverwrite(
        view_channel=True, connect=True, speak=True
    )
    prison_voice_overwrites[guard_role] = discord.PermissionOverwrite(
        view_channel=True, connect=True, speak=True
    )
    await prison_voice.edit(
        overwrites=prison_voice_overwrites, reason="Prison bot setup"
    )

    guild_config["prisonRoleId"] = prison_role.id
    guild_config["guardRoleId"] = guard_role.id
    guild_config["prisonCategoryId"] = category.id
    guild_config["prisonTextChannelId"] = prison_text.id
    guild_config["prisonVoiceChannelId"] = prison_voice.id

    return {
        "guild_config": guild_config,
        "prison_role": prison_role,
        "guard_role": guard_role,
        "prison_text": prison_text,
        "prison_voice": prison_voice,
    }


async def apply_prison_overwrites(
    guild: discord.Guild, member: discord.Member, guild_config: dict
) -> dict:
    skip_ids = {
        guild_config.get("prisonCategoryId"),
        guild_config.get("prisonTextChannelId"),
        guild_config.get("prisonVoiceChannelId"),
    }

    overwrites = {}
    for channel in guild.channels:
        if channel.id in skip_ids:
            continue
        if isinstance(channel, discord.Thread):
            continue

        current = channel.overwrites.get(member)
        if current is None:
            overwrites[str(channel.id)] = None
        else:
            allow, deny = current.pair()
            overwrites[str(channel.id)] = {
                "allow": allow.value,
                "deny": deny.value,
            }

        await channel.set_permissions(
            member, view_channel=False, reason="Imprisoned by prison bot"
        )

    return overwrites


async def restore_prison_overwrites(
    guild: discord.Guild, member: discord.Member, overwrites: dict
) -> None:
    if not overwrites:
        return
    for channel_id, previous in overwrites.items():
        channel = guild.get_channel(int(channel_id))
        if not channel or isinstance(channel, discord.Thread):
            continue

        if previous is None:
            await channel.set_permissions(member, overwrite=None)
            continue

        allow = discord.Permissions(previous["allow"])
        deny = discord.Permissions(previous["deny"])
        overwrite = discord.PermissionOverwrite.from_pair(allow, deny)
        await channel.set_permissions(member, overwrite=overwrite)


@bot.event
async def on_ready() -> None:
    print(f"Logged in as {bot.user}")
    print(f"Connected to {len(bot.guilds)} guild(s)")
    if bot.guilds:
        guild_list = ", ".join(f"{guild.name} ({guild.id})" for guild in bot.guilds)
        print(f"Connected to guilds: {guild_list}")
    await register_commands()


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return
    if message.content.startswith("!"):
        print(
            f"Chat command from {message.author} in "
            f"{message.guild.name if message.guild else 'DM'}: {message.content}"
        )

    await bot.process_commands(message)


@bot.tree.command(name="setup", description="Create or update prison roles and channels")
async def setup(interaction: discord.Interaction) -> None:
    if not interaction.guild or not interaction.user:
        await interaction.response.send_message(
            "Commands can only be used in a server.", ephemeral=True
        )
        return

    member = interaction.guild.get_member(interaction.user.id)
    if not member or not is_admin(member):
        await interaction.response.send_message(
            "Only admins can run setup.", ephemeral=True
        )
        return

    bot_perm_error = ensure_bot_permissions(interaction.guild)
    if bot_perm_error:
        await interaction.response.send_message(bot_perm_error, ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    config = load_config()
    result = await ensure_setup(interaction.guild, config)
    save_config(config)

    await interaction.followup.send(
        "Prison setup complete.\n"
        f"Prison role: <@&{result['prison_role'].id}>\n"
        f"Guard role: <@&{result['guard_role'].id}>\n"
        f"Text: <#{result['prison_text'].id}>\n"
        f"Voice: <#{result['prison_voice'].id}>",
        ephemeral=True,
    )


async def imprison_member(
    guild: discord.Guild,
    actor: discord.Member,
    target: discord.Member,
    reason: str | None,
) -> tuple[bool, str]:
    config = load_config()
    guild_config = get_guild_config(config, guild.id)

    if not is_guard_or_admin(actor, guild_config):
        return False, "You must be a prison guard or admin."

    bot_perm_error = ensure_bot_permissions(guild)
    if bot_perm_error:
        return False, bot_perm_error

    if target.id == actor.id:
        return False, "You cannot imprison yourself."

    if is_admin(target):
        return False, "You cannot imprison an admin."

    required_keys = (
        "prisonRoleId",
        "guardRoleId",
        "prisonTextChannelId",
        "prisonVoiceChannelId",
    )
    if not all(guild_config.get(k) for k in required_keys):
        return False, "Prison is not set up. Run /setup first."

    if str(target.id) in guild_config["prisoners"]:
        return False, "This member is already in prison."

    prison_role = guild.get_role(guild_config["prisonRoleId"])
    if not prison_role:
        return False, "Prison role is missing. Run /setup."

    bot_member = guild.me
    if not bot_member:
        return False, "Bot not in guild."

    if bot_member.top_role <= target.top_role:
        return False, "I cannot imprison this member due to role hierarchy."
    if bot_member.top_role <= prison_role:
        return False, "Move my role above the Prisoner role."

    roles_to_remove = [
        role
        for role in target.roles
        if role.id != guild.id
        and not role.managed
        and role.id != prison_role.id
        and role.position < bot_member.top_role.position
    ]
    role_ids = [role.id for role in roles_to_remove]
    if role_ids:
        await target.remove_roles(*roles_to_remove, reason="Imprisoned by prison bot")

    await target.add_roles(prison_role, reason="Imprisoned by prison bot")

    overwrites = await apply_prison_overwrites(guild, target, guild_config)

    guild_config["prisoners"][str(target.id)] = {
        "roles": role_ids,
        "overwrites": overwrites,
        "reason": reason or "No reason given",
        "moderatorId": actor.id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    save_config(config)

    return True, f"Imprisoned <@{target.id}>. Reason: {reason or 'No reason given'}"


async def release_member(
    guild: discord.Guild, actor: discord.Member, target: discord.Member
) -> tuple[bool, str]:
    config = load_config()
    guild_config = get_guild_config(config, guild.id)

    if not is_guard_or_admin(actor, guild_config):
        return False, "You must be a prison guard or admin."

    bot_perm_error = ensure_bot_permissions(guild)
    if bot_perm_error:
        return False, bot_perm_error

    record = guild_config["prisoners"].get(str(target.id))
    prison_role_id = guild_config.get("prisonRoleId")
    prison_role = guild.get_role(prison_role_id) if prison_role_id else None
    if not record and (not prison_role or prison_role not in target.roles):
        return False, "This member is not in prison."

    if prison_role and prison_role in target.roles:
        await target.remove_roles(prison_role, reason="Released by prison bot")

    if record and record.get("roles"):
        bot_member = guild.me
        if bot_member:
            roles_to_restore = []
            for role_id in record["roles"]:
                role = guild.get_role(role_id)
                if role and role.position < bot_member.top_role.position:
                    roles_to_restore.append(role)
            if roles_to_restore:
                await target.add_roles(
                    *roles_to_restore, reason="Released by prison bot"
                )

    if record and record.get("overwrites"):
        await restore_prison_overwrites(guild, target, record["overwrites"])

    if record:
        del guild_config["prisoners"][str(target.id)]
        save_config(config)

    return True, f"Released <@{target.id}>."


@bot.tree.command(name="setguard", description="Set the prison guard role")
@app_commands.describe(role="Guard role")
async def setguard(interaction: discord.Interaction, role: discord.Role) -> None:
    if not interaction.guild or not interaction.user:
        await interaction.response.send_message(
            "Commands can only be used in a server.", ephemeral=True
        )
        return

    member = interaction.guild.get_member(interaction.user.id)
    if not member or not is_admin(member):
        await interaction.response.send_message(
            "Only admins can set the guard role.", ephemeral=True
        )
        return

    config = load_config()
    guild_config = get_guild_config(config, interaction.guild.id)
    guild_config["guardRoleId"] = role.id
    save_config(config)

    await interaction.response.send_message(
        f"Guard role set to <@&{role.id}>.", ephemeral=True
    )


@bot.tree.command(name="prison", description="Send a member to prison")
@app_commands.describe(member="Member", reason="Reason")
async def prison(
    interaction: discord.Interaction, member: discord.Member, reason: str | None = None
) -> None:
    if not interaction.guild or not interaction.user:
        await interaction.response.send_message(
            "Commands can only be used in a server.", ephemeral=True
        )
        return

    caller = interaction.guild.get_member(interaction.user.id)
    await interaction.response.defer(ephemeral=True)
    if not caller:
        await interaction.followup.send("Caller not found.", ephemeral=True)
        return

    ok, message = await imprison_member(interaction.guild, caller, member, reason)
    await interaction.followup.send(message, ephemeral=True)


@bot.tree.command(name="release", description="Release a member from prison")
@app_commands.describe(member="Member")
async def release(interaction: discord.Interaction, member: discord.Member) -> None:
    if not interaction.guild or not interaction.user:
        await interaction.response.send_message(
            "Commands can only be used in a server.", ephemeral=True
        )
        return

    caller = interaction.guild.get_member(interaction.user.id)
    await interaction.response.defer(ephemeral=True)
    if not caller:
        await interaction.followup.send("Caller not found.", ephemeral=True)
        return

    ok, message = await release_member(interaction.guild, caller, member)
    await interaction.followup.send(message, ephemeral=True)


@bot.command(name="prison")
@commands.guild_only()
async def prison_command(
    ctx: commands.Context, member: discord.Member, *, reason: str | None = None
) -> None:
    if not isinstance(ctx.author, discord.Member):
        return

    ok, message = await imprison_member(ctx.guild, ctx.author, member, reason)
    await ctx.send(message)


@bot.command(name="release")
@commands.guild_only()
async def release_command(ctx: commands.Context, member: discord.Member) -> None:
    if not isinstance(ctx.author, discord.Member):
        return

    ok, message = await release_member(ctx.guild, ctx.author, member)
    await ctx.send(message)


@bot.command(name="setup")
@commands.guild_only()
async def setup_command(ctx: commands.Context) -> None:
    if not isinstance(ctx.author, discord.Member):
        return

    if not is_admin(ctx.author):
        await ctx.send("Only admins can run setup.")
        return

    bot_perm_error = ensure_bot_permissions(ctx.guild)
    if bot_perm_error:
        await ctx.send(bot_perm_error)
        return

    config = load_config()
    result = await ensure_setup(ctx.guild, config)
    save_config(config)

    await ctx.send(
        "Prison setup complete.\n"
        f"Prison role: <@&{result['prison_role'].id}>\n"
        f"Guard role: <@&{result['guard_role'].id}>\n"
        f"Text: <#{result['prison_text'].id}>\n"
        f"Voice: <#{result['prison_voice'].id}>"
    )


@bot.command(name="setguard")
@commands.guild_only()
async def setguard_command(ctx: commands.Context, role: discord.Role) -> None:
    if not isinstance(ctx.author, discord.Member):
        return

    if not is_admin(ctx.author):
        await ctx.send("Only admins can set the guard role.")
        return

    config = load_config()
    guild_config = get_guild_config(config, ctx.guild.id)
    guild_config["guardRoleId"] = role.id
    save_config(config)

    await ctx.send(f"Guard role set to <@&{role.id}>.")


@bot.event
async def on_command_error(ctx: commands.Context, error: Exception) -> None:
    if isinstance(error, commands.NoPrivateMessage):
        await ctx.send("This command only works in a server.")
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Missing required argument.")
        return
    if isinstance(error, commands.BadArgument):
        await ctx.send("Invalid argument.")
        return
    print(f"Command error: {error}")


bot.run(TOKEN)
