# Prospere Bot

A Discord community bot for tracking social-platform statistics and running moderation, welcome, level, application, ticket, bus, and reminder features.

## Features

- YouTube, TikTok, and Twitch profile tracking
- Configurable subscriber status embed and history graph
- YouTube and Twitch live upload alerts
- Discord welcome messages and automatic roles
- Counting, XP levels, leaderboards, and AFK status
- Applications, support tickets, reaction roles, and role panels
- School portal and Sparx Maths links
- Bus and tram departure displays
- Polls, reminders, announcements, moderation, backups, and utility commands
- Optional Gemini-powered AI, translation, summary, and explanation commands

## Requirements

- Linux, macOS, Windows, or a Raspberry Pi
- Python 3.10 or newer
- A Discord application and bot account
- A YouTube Data API v3 key
- Optional Twitch application credentials
- Optional Google Gemini API key

## Discord setup

1. Open the [Discord Developer Portal](https://discord.com/developers/applications).
2. Create an application, then add a bot under **Bot**.
3. Enable **Server Members Intent** and **Message Content Intent**.
4. Reset and copy the bot token if needed.
5. Invite the bot using the OAuth2 URL generator. Select `bot` and `applications.commands`, then grant at least:
   - View Channels
   - Send Messages
   - Read Message History
   - Embed Links
   - Manage Messages
   - Manage Channels
   - Add Reactions
6. Make sure channel permissions do not deny the bot any required permissions.

## Install

From Linux or macOS:

```bash
cd /home/test124/Games/umu
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
```

On Windows PowerShell:

```powershell
cd path\to\umu
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` and replace every placeholder with your own credentials. `.env` is ignored by Git and must never be committed.

## Configuration

### Required values

```dotenv
DISCORD_TOKEN=your_discord_bot_token
YOUTUBE_API_KEY=your_youtube_data_api_key
DISCORD_CHANNEL_ID=1551281250348564510
UPDATE_INTERVAL_MINUTES=30
```

### Optional Twitch values

Create an application in the [Twitch Developer Console](https://dev.twitch.tv/console/apps), then add:

```dotenv
TWITCH_CLIENT_ID=your_twitch_client_id
TWITCH_CLIENT_SECRET=your_twitch_client_secret
```

Both values must come from the same Twitch application. If Twitch reports `invalid client`, generate a new secret and update both values.

### Optional Gemini values

Create a Google Gemini API key and add:

```dotenv
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-3.5-flash
```

## Start the bot

Linux/macOS:

```bash
.venv/bin/python bot.py
```

Or:

```bash
./start_bot.sh
```

Windows:

```powershell
.venv\Scripts\python.exe bot.py
```

Keep the terminal open while running the bot. The first startup can take a minute while Discord slash commands are registered.

### Run on Raspberry Pi as a service

From inside the bot directory:

```bash
sudo chmod +x install_pi_service.sh
sudo ./install_pi_service.sh
sudo systemctl status youtube-subscriber-discord-bot
sudo journalctl -u youtube-subscriber-discord-bot -f
```

## Main commands

Run `/help` in Discord for the bot's current command list.

| Command | Purpose |
|---|---|
| `/subs` | Refresh the subscriber status immediately |
| `/graph days:30` | Show subscriber history |
| `/config-channel channel:#channel` | Select the tracker channel |
| `/profile-add` | Add or replace a YouTube, Twitch, or TikTok profile |
| `/profile-remove name:Name` | Remove a profile |
| `/profile-list` | List configured profiles |
| `/welcome-config channel:#welcome` | Configure welcome messages |
| `/autorole-config role:@Role` | Configure automatic roles |
| `/counting-config channel:#counting` | Configure the counting channel |
| `/monitor-config channel:#alerts` | Configure upload and live alerts |
| `/bus-config` | Configure bus departure tracking |
| `/ticket-panel` | Create a support-ticket panel |
| `/application-config` | Configure applications and staff reviews |
| `/level` | Show a member's XP level |
| `/leaderboard` | Show the server XP leaderboard |
| `/poll question:Question` | Create a poll |
| `/remind minutes:30 message:Reminder` | Schedule a direct-message reminder |
| `/uptime` | Show bot uptime |
| `/bot-status` | Show bot status |

Settings are saved in `config.json`. Runtime state is stored in `state.json` and subscriber history in `subscriber_history.json`. These files are ignored by Git so personal data and credentials are not committed.

## Troubleshooting

### Discord login fails

- Copy a new token from the Discord Developer Portal.
- Replace the entire `DISCORD_TOKEN` value in `.env`.
- Restart the bot.
- If a previously exposed token was posted anywhere, reset it immediately.

### `PrivilegedIntentsRequired`

Enable both Server Members Intent and Message Content Intent in the Discord Developer Portal under **Bot**, then restart the bot.

### Slash commands do not appear

Global Discord commands can take several minutes to appear. Confirm the bot was invited with `applications.commands` and check that it has permission to use application commands in the server.

### Twitch authentication fails

Confirm that `TWITCH_CLIENT_ID` and `TWITCH_CLIENT_SECRET` belong to the same Twitch application, and do not use the example placeholder values.

### A status message cannot be edited

Discord only permits a bot to edit its own messages. Remove the old stored `message_id` from `state.json` or run `/subs`; the bot will create a fresh status message. If `state.json` is accidentally deleted, the bot will create a new tracker message.

### Missing message permissions

Check both server role permissions and channel permission overrides. The bot needs permission in the exact channel used by each feature.

## Security

Never place credentials in source files or commit `.env`, Discord tokens, API keys, or OAuth secrets. Keep `state.json`, `subscriber_history.json`, and `config.json` private because local configuration may contain server IDs and community data.
