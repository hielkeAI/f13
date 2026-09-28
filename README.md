# YouTube subscriber Discord bot

This bot keeps one embed updated in Discord channel `1551281250348564510` with subscriber counts for:

- [Unlimited Pro](https://www.youtube.com/@unlimtedpro/shorts)
- [F13-YT](https://www.youtube.com/@F13-YT)

It also tracks the supplied F13 and Unlimited TikTok and Twitch profiles. TikTok follower counts use best-effort public profile parsing and may be unavailable when TikTok changes its page or blocks automated requests. Twitch follower counts use the Twitch API.

## Setup

1. Create a Discord application and bot at the [Discord Developer Portal](https://discord.com/developers/applications).
2. Enable **Server Members Intent** and **Message Content Intent** under the bot's privileged gateway intents.
3. Invite the bot with the `bot` and `applications.commands` scopes. Give it these bot permissions:
   - View Channels
   - Send Messages
   - Read Message History
   - Embed Links
   - Attach Files
   - Add Reactions
   - Use External Emojis
   - Manage Channels (tickets and channel locks)
   - Manage Messages (counting cleanup, if enabled)
4. In every channel used by a feature, check **Edit Channel → Permissions** and allow the bot role those permissions. Channel overrides can block a bot even when its server role has permission.
5. Create a YouTube Data API v3 key in Google Cloud and enable the YouTube Data API v3 for that project.
6. Create a virtual environment and install dependencies:

   ```bash
   python3 -m venv .venv
   . .venv/bin/activate
   python -m pip install -r requirements.txt
   ```

7. Copy `.env.example` to `.env` and fill in `DISCORD_TOKEN`, `YOUTUBE_API_KEY`, `TWITCH_CLIENT_ID`, and `TWITCH_CLIENT_SECRET`. Create Twitch credentials at the [Twitch Developer Console](https://dev.twitch.tv/console/apps).
8. Start the bot:

   ```bash
   . .venv/bin/activate
   python bot.py
   ```

   Or use the project launcher from any directory:

   ```bash
   /home/test124/Games/umu/start_bot.sh
   ```

The bot updates every 30 minutes by default. Use `/subs` in the configured Discord channel for an immediate refresh, or `/graph` to send a zoomed chart for the last 30 days. Use `/graph days:90` (up to 3,650 days) for a longer history. Change `UPDATE_INTERVAL_MINUTES` in `.env` to adjust the schedule.

Commands are synchronized globally, so they become available in every server where the bot is installed. Global Discord command updates can take a little time to appear after a restart.

## Configuration commands

Server members with **Manage Server** permission can use:

- `/config-channel channel:#channel` changes the tracker channel.
- `/profile-add platform:YouTube name:F13 handle:F13-YT url:https://www.youtube.com/@F13-YT` adds or replaces a profile. The platform choices are YouTube, Twitch, and TikTok.
- `/profile-remove name:F13` removes a profile by display name.
- `/profile-list` shows all configured profiles.
- `/bus-config stop_url:https://bustimes.org/stops/STOP_CODE route:184 channel:#bus-times` creates a next-departures display for route 184 at that stop.
- `/bus-add stop_name:Piccadilly Gardens route:184 channel:#bus-times` searches Bustimes by stop name and creates the display. Use route `*` to show every bus at the stop.
- `/bus-refresh` manually refreshes the Bustimes display.
- `/counting-config channel:#counting` enables counting in that channel. Omit the channel to disable it.
- `/welcome-config channel:#welcome image_url:https://...` enables the new-member welcomer with a Transdev Route 36 bus image. Omit the channel to disable it.
- `/test-welcome` posts a welcome preview using your avatar and the bus image.
- `/monitor-config channel:#alerts youtube_uploads:true twitch_live:true` enables YouTube upload and Twitch live alerts. Omit the channel to disable alerts.
- `/level` shows your XP level. Members gain XP from messages, with a one-minute cooldown.
- `/leaderboard` shows the top ten members by XP.
- `/help` lists the main commands.
- `/sparx` posts Sparx Maths, Sparx Science, and Sparx Reader links.
- `/sparx-disable` disables Sparx links for the server, and `/sparx-enable` restores them.
- `/sparx-config maths_url:https://... science_url:https://... reader_url:https://...` sets school-specific Sparx portals.
- `/school-portals` compares Sparx, Dr Frost Maths, Arbor, SIMS, Bromcom, and ClassCharts.
- `/school-portals-config sparx_url:https://... dr_frost_url:https://... arbor_url:https://... sims_url:https://... bromcom_url:https://... classcharts_url:https://...` sets your school's exact login links.
- `/serverinfo`, `/userinfo`, and `/avatar` show server/member information.
- `/poll question:...` creates a yes/no poll.
- `/remind minutes:30 message:...` sends you a direct-message reminder.
- `/uptime` and `/bot-status` show service health.
- `/timestamp date_time:21/09/2026 18:30 style:Date and time` creates a Discord timestamp in UK format.
- `/autorole-config role:@Member` gives new members a role automatically. Omit the role to disable it.
- `/role-panel role:@Role` posts a button for members to toggle a role.
- `/reaction-role message_id:123 emoji:🎮 role:@Gamer` creates an emoji reaction role; `/reaction-role-remove` removes one.
- `/roll`, `/coinflip`, `/choose`, and `/8ball` add simple fun commands.
- `/announce channel:#news message:...` sends a staff announcement.
- `/clear amount:25` deletes recent messages.
- `/moderation-config anti_spam:true banned_words:word1,word2` enables moderation.
- `/backup` sends a JSON backup of bot data to the administrator.
- `/error-log-config channel:#bot-errors` enables slash-command error logging.

Use `/subs` after adding a profile to fetch its count immediately. Settings are saved in `config.json`.

## Discord intent checklist

In **Developer Portal → Bot → Privileged Gateway Intents**, enable:

- **Server Members Intent** for welcomes and member counts.
- **Message Content Intent** for counting and XP levels.

After enabling intents, restart the bot. If Discord returns `4014 PrivilegedIntentsRequired`, one of these two switches is still disabled.

## Twitch troubleshooting

If the log says `Twitch authentication failed: invalid client`, the `TWITCH_CLIENT_ID` and `TWITCH_CLIENT_SECRET` do not belong to the same Twitch application. Create or open one app in the [Twitch Developer Console](https://dev.twitch.tv/console/apps), copy its Client ID and generate a new secret, then replace both values in `.env`. Never put them in `.env.example` or post them in chat.

## Channel locking

Members with **Manage Channels** can lock the current text channel with:

```text
/hielkemap ban notimetospeak
```

Use `/hielkemap unlock` in the locked channel to restore normal speaking permissions. The bot needs **Manage Channels** permission.

For Bustimes, open the exact stop on [bustimes.org](https://bustimes.org), copy its stop-page URL, and pass that URL to `/bus-config`. A route number such as `184` is not enough by itself because the route has many stops. The bot displays the next five scheduled/expected departures and refreshes every five minutes.

## Support tickets

1. Give the bot **Manage Channels**, **View Channels**, **Send Messages**, and **Read Message History** permissions.
2. A server manager runs `/ticket-panel` in the channel where the panel should appear.
3. Members click **Open Ticket**. The bot creates a private ticket channel for them.
4. The ticket owner or a moderator with **Manage Channels** clicks **Close Ticket**, or runs `/ticket-close` inside the ticket.

`state.json` stores the Discord message ID and last known counts. Historical samples are stored separately in `subscriber_history.json` (up to 1,000 samples), so they survive bot restarts and status-message changes. Both files are ignored by git.

The Discord token must be kept only in `.env`. The token posted in chat should be regenerated in the Discord Developer Portal before starting the bot.
