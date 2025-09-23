<div align="center">

# Radiosonde Telegram Bot

A Telegram bot that monitors and tracks radiosondes (weather balloons) in real-time using data from SondeHub. The bot sends alerts when radiosondes enter a specified monitoring area and provides detailed information about their position, altitude, and movement.

</div>

## Features

- **Real-time Monitoring**: Connects to SondeHub's live data stream to track radiosondes worldwide
- **Geofenced Alerts**: Notifies users when radiosondes enter a defined geographical area
- **Multi-language Support**: Available in both English and Romanian versions
- **Smart Notification System**: Configurable alerts for initial detection, position updates, and landing events
- **Historical Data Tracking**: Saves complete history of all detected radiosondes
- **User Management**: Subscription system for multiple users with admin controls
- **Interactive Commands**: Full Telegram bot interface with various commands for status and information

<div align="center">

## ☕ [Support my work on Ko-Fi](https://ko-fi.com/thatsinewave)

</div>

## How It Works

The bot continuously monitors the SondeHub data stream for radiosonde transmissions. When a radiosonde is detected within the configured geographical area and altitude range, it:

1. Sends an initial detection alert to subscribed users
2. Periodically sends position updates (configurable interval)
3. Sends a landing alert when the radiosonde descends below 100 meters
4. Logs all data to local history files for future reference

## Setup Instructions

### Prerequisites

- Python 3.7 or higher
- Telegram Bot Token (obtain from [@BotFather](https://t.me/BotFather))
- Your Telegram Chat ID (use [@userinfobot](https://t.me/userinfobot) to find it)

### Installation

1. Clone or download the project files
2. Install required dependencies:
   ```
   pip install aiohttp sondehub
   ```

3. Configure the bot by editing `config.json`:
   ```json
   {
     "telegram": {
       "bot_token": "YOUR_BOT_TOKEN_HERE",
       "admin_chat_id": "YOUR_CHAT_ID_HERE"
     },
     "monitoring": {
       "target_latitude": 44.4268,
       "target_longitude": 26.1025,
       "radius_km": 15.0,
       "check_interval_seconds": 60,
       "min_altitude_m": 0,
       "max_altitude_m": 30000
     },
     "notification_settings": {
       "send_initial_detection": true,
       "send_position_updates": true,
       "send_landing_alert": true,
       "update_interval_minutes": 5
     }
   }
   ```

4. Choose your preferred language version:
   - Use `notifier_EN.py` for English
   - Use `notifier_RO.py` for Romanian

5. Run the bot:
   ```
   python notifier_EN.py
   ```
   or
   ```
   python notifier_RO.py
   ```

<div align="center">

# [Join my discord server](https://thatsinewave.github.io/Discord-Redirect/)

</div>

### Configuration Details

- **target_latitude/target_longitude**: The center point of your monitoring area
- **radius_km**: The radius (in kilometers) from the center point to monitor
- **min_altitude_m/max_altitude_m**: Altitude range filter (in meters)
- **update_interval_minutes**: How frequently to send position update notifications

## Telegram Commands

- `/start` - Subscribe to radiosonde notifications
- `/stop` - Unsubscribe from notifications
- `/status` - Show current monitoring status
- `/list` - List all currently tracked sondes
- `/history <serial>` - Show history for a specific sonde
- `/help` - Show help message with all commands
- `/subscribers` - (Admin only) List all subscribed users

## File Structure

```
radiosonde-notifier/
├── notifier_EN.py          # English version of the bot
├── notifier_RO.py          # Romanian version of the bot
├── config.json             # Configuration file
├── subscriptions.json      # User subscriptions (auto-generated)
└── history/                # Data directory (auto-generated)
    ├── bot/
    │   └── radiosonde_notifier.log  # Bot operation log
    └── sondes/
        └── [serial].log    # Individual sonde history files
```

## Data Privacy

- The bot only stores necessary user data (chat ID and name) for notification purposes
- Users can unsubscribe at any time using the `/stop` command
- All data is stored locally and not shared with third parties

## Troubleshooting

1. **Bot doesn't start**: Check that your bot token and chat ID are correctly configured
2. **No notifications**: Verify your monitoring area covers active radiosonde launch sites
3. **Encoding issues**: Use the Romanian version if you need special character support

## Contributing

Feel free to fork this project and submit pull requests for any improvements. The bot is designed to be extensible with additional features and language support.

## License

This project is open source and available under the GNU General Public License.
