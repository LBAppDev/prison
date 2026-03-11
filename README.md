# Prison Bot (Python)

Discord bot that lets prison guards send members to prison and release them. Prison is one text channel and one voice channel. Prisoners only see those channels; guards and admins see them too.

## Setup

1. Install dependencies:

```bash
python -m pip install -r requirements.txt
```

2. Create `.env` from `.env.example` and set `DISCORD_TOKEN`.
3. Invite the bot with permissions: Manage Roles, Manage Channels.
4. Start the bot:

```bash
python bot.py
```

## Commands

- `/setup` Create or update the Prison roles and channels.
- `/setguard` Choose the Prison Guard role.
- `/prison` Send a member to prison.
- `/release` Release a member from prison.
- `!prison @user [reason]` Send a member to prison (chat command).
- `!release @user` Release a member from prison (chat command).
- `!setup` Create or update the Prison roles and channels (chat command).
- `!setguard @role` Choose the Prison Guard role (chat command).

## Notes

- The bot stores prisoner roles and permission overwrites in `data/config.json`.
- Role hierarchy matters: the bot's role must be higher than the Prisoner role and the target's roles.
- Enable the Message Content intent in the Discord Developer Portal for chat commands.
