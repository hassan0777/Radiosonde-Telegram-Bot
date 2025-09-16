# Radiosonde Notifier

<div align="center">

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
- **Web Dashboard**: Comprehensive web-based dashboard for monitoring and data visualization

<div align="center">

## ☕ [Support my work on Ko-Fi](https://ko-fi.com/thatsinewave)

</div>

## How It Works

The bot continuously monitors the SondeHub data stream for radiosonde transmissions. When a radiosonde is detected within the configured geographical area and altitude range, it:

1. Sends an initial detection alert to subscribed users
2. Periodically sends position updates (configurable interval)
3. Sends a landing alert when the radiosonde descends below 100 meters
4. Logs all data to local history files for future reference
5. Updates the web dashboard with real-time information

## Setup Instructions

### Prerequisites

- Python 3.7 or higher
- Telegram Bot Token (obtain from [@BotFather](https://t.me/BotFather))
- Your Telegram Chat ID (use [@userinfobot](https://t.me/userinfobot) to find it)
- Web server (for dashboard hosting, optional)

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

6. (Optional) Deploy the web dashboard:
   - Copy `Dashboard_EN.html` or `Dashboard_RO.html` to your web server
   - Ensure the dashboard can access the generated JSON files (config.json, subscriptions.json, and history files)

<div align="center">

# [Join my discord server](https://thatsinewave.github.io/Discord-Redirect/)

</div>

### Configuration Details

- **target_latitude/target_longitude**: The center point of your monitoring area
- **radius_km**: The radius (in kilometers) from the center point to monitor
- **min_altitude_m/max_altitude_m**: Altitude range filter (in meters)
- **update_interval_minutes**: How frequently to send position update notifications

## Web Dashboard

The Radiosonde Notifier includes a comprehensive web dashboard for monitoring and data visualization:

### Dashboard Features

- **Real-time Map Visualization**: View sonde locations on interactive maps with multiple style options
- **Data Analytics**: Charts for altitude, speed, vertical velocity, and direction analysis
- **File Management**: View and manage configuration, subscriptions, and log files
- **User Management**: Monitor and manage subscribed users
- **Statistics Panel**: View monitoring statistics and bot performance metrics
- **Multi-language Support**: Available in both English and Romanian versions

### Accessing the Dashboard

1. Choose your preferred language version:
   - English: Open `Dashboard_EN.html` in a web browser
   - Romanian: Open `Dashboard_RO.html` in a web browser

2. The dashboard will automatically load and display data from:
   - `config.json` - Bot configuration
   - `subscriptions.json` - User subscription data
   - `history/bot/radiosonde_notifier.log` - Bot operation log
   - `history/sondes/` - Individual sonde history files

### Dashboard Components

- **Map Panel**: Interactive map showing sonde locations with different base layers (Dark, Light, Terrain, Satellite)
- **Chart Panels**: Four analytical charts showing:
  - Altitude vs Time
  - Horizontal Speed vs Time
  - Vertical Velocity vs Time
  - Direction Analysis
- **File Explorer**: Sidebar for navigating configuration and data files
- **Statistics**: Real-time monitoring statistics and subscriber information

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
├── Dashboard_EN.html       # English dashboard
├── Dashboard_RO.html       # Romanian dashboard
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
- The dashboard runs entirely client-side and does not transmit data to external servers

## Troubleshooting

1. **Bot doesn't start**: Check that your bot token and chat ID are correctly configured
2. **No notifications**: Verify your monitoring area covers active radiosonde launch sites
3. **Encoding issues**: Use the Romanian version if you need special character support
4. **Dashboard not loading data**: Ensure the dashboard HTML file is served from the same directory as the data files or configure CORS if hosting separately

## Contributing

Feel free to fork this project and submit pull requests for any improvements. The bot is designed to be extensible with additional features and language support.

## License

This project is open source and available under the GNU General Public License.