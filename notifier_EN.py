import json
import logging
import time
import math
import asyncio
import os
from datetime import datetime, timedelta
import aiohttp
import sondehub

# Configure logging
# We'll create the log directory in the __init__ method
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)


class RadiosondeNotifier:
    def __init__(self, config_file="config.json"):
        self.config = self.load_config(config_file)
        self.telegram_config = self.config["telegram"]
        self.monitoring_config = self.config["monitoring"]
        self.notification_settings = self.config["notification_settings"]

        # Track detected radiosondes to avoid duplicate notifications
        self.detected_sonde = {}
        self.last_notification_time = {}

        # Initialize SondeHub client
        self.sondehub_stream = None

        # Create history directory structure
        self.history_dir = "history"
        self.sondes_dir = os.path.join(self.history_dir, "sondes")
        self.bot_dir = os.path.join(self.history_dir, "bot")

        os.makedirs(self.sondes_dir, exist_ok=True)
        os.makedirs(self.bot_dir, exist_ok=True)

        # Set up file logging to the bot directory
        self.setup_logging()

        # For Telegram command handling
        self.last_update_id = 0

        # Load user subscriptions
        self.subscriptions_file = "subscriptions.json"
        self.subscribed_users = self.load_subscriptions()

    def setup_logging(self):
        """Set up file logging to the bot directory"""
        # Remove any existing file handlers
        for handler in logging.root.handlers[:]:
            if isinstance(handler, logging.FileHandler):
                logging.root.removeHandler(handler)

        # Add file handler to bot directory
        log_file = os.path.join(self.bot_dir, "radiosonde_notifier.log")
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        )
        logging.root.addHandler(file_handler)

    def load_config(self, config_file):
        """Load configuration from JSON file"""
        try:
            with open(config_file, "r") as f:
                return json.load(f)
        except FileNotFoundError:
            logging.error(f"Config file {config_file} not found!")
            raise
        except json.JSONDecodeError:
            logging.error(f"Invalid JSON in config file {config_file}!")
            raise

    def load_subscriptions(self):
        """Load user subscriptions from file"""
        try:
            if os.path.exists(self.subscriptions_file):
                with open(self.subscriptions_file, "r") as f:
                    return json.load(f)
            return {}
        except Exception as e:
            logging.error(f"Error loading subscriptions: {e}")
            return {}

    def save_subscriptions(self):
        """Save user subscriptions to file"""
        try:
            with open(self.subscriptions_file, "w") as f:
                json.dump(self.subscribed_users, f, indent=2)
        except Exception as e:
            logging.error(f"Error saving subscriptions: {e}")

    def haversine_distance(self, lat1, lon1, lat2, lon2):
        """Calculate the great-circle distance between two points on Earth"""
        R = 6371  # Earth radius in kilometers

        lat1_rad = math.radians(lat1)
        lon1_rad = math.radians(lon1)
        lat2_rad = math.radians(lat2)
        lon2_rad = math.radians(lon2)

        dlat = lat2_rad - lat1_rad
        dlon = lon2_rad - lon1_rad

        a = (
            math.sin(dlat / 2) ** 2
            + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
        )
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

        return R * c

    def is_within_radius(self, sonde_lat, sonde_lon):
        """Check if radiosonde is within monitoring radius"""
        target_lat = self.monitoring_config["target_latitude"]
        target_lon = self.monitoring_config["target_longitude"]
        radius_km = self.monitoring_config["radius_km"]

        distance = self.haversine_distance(target_lat, target_lon, sonde_lat, sonde_lon)
        return distance <= radius_km, distance

    def is_within_altitude(self, altitude):
        """Check if radiosonde is within altitude range"""
        min_alt = self.monitoring_config["min_altitude_m"]
        max_alt = self.monitoring_config["max_altitude_m"]

        if altitude is None:
            return False
        return min_alt <= altitude <= max_alt

    def format_telegram_message(self, sonde_data, distance_km, event_type):
        """Format detailed Telegram message"""
        serial = sonde_data.get("serial", "Unknown")
        lat = sonde_data.get("lat", 0)
        lon = sonde_data.get("lon", 0)
        alt = sonde_data.get("alt", 0)
        velocity_h = sonde_data.get("vel_h", 0)
        velocity_v = sonde_data.get("vel_v", 0)
        frequency = sonde_data.get("frequency", "Unknown")
        datetime_str = sonde_data.get("datetime", "")

        # Convert datetime to readable format
        try:
            dt = datetime.fromisoformat(datetime_str.replace("Z", "+00:00"))
            time_str = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        except:
            time_str = datetime_str

        emoji = (
            "🚀" if event_type == "initial" else "📡" if event_type == "update" else "🪂"
        )

        message = f"{emoji} *Radiosonde Alert* {emoji}\n\n"
        message += f"*Event:* {'Detection' if event_type == 'initial' else 'Update' if event_type == 'update' else 'Landing'}\n"
        message += f"*Serial:* `{serial}`\n"
        message += f"*Distance:* {distance_km:.1f} km from target\n"
        message += f"*Position:* {lat:.4f}°, {lon:.4f}°\n"
        message += f"*Altitude:* {alt:.0f} m\n"
        message += f"*Horizontal Speed:* {velocity_h:.1f} m/s\n"
        message += f"*Vertical Speed:* {velocity_v:.1f} m/s\n"
        message += f"*Frequency:* {frequency}\n"
        message += f"*Last Update:* {time_str}\n\n"

        # Add Google Maps link
        maps_link = f"https://maps.google.com/?q={lat},{lon}"
        message += f"📍 [View on Google Maps]({maps_link})"

        return message

    async def send_telegram_message(self, message, chat_id=None, reply_markup=None):
        """Send message to Telegram"""
        if chat_id is None:
            # Send to all subscribed users
            for user_id in self.subscribed_users.keys():
                await self._send_to_user(message, user_id, reply_markup)
        else:
            # Send to specific user
            await self._send_to_user(message, chat_id, reply_markup)

    async def _send_to_user(self, message, chat_id, reply_markup=None):
        """Send message to a specific user"""
        url = f"https://api.telegram.org/bot{self.telegram_config['bot_token']}/sendMessage"

        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }

        if reply_markup:
            payload["reply_markup"] = reply_markup

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as response:
                    if response.status == 200:
                        logging.info(f"Telegram message sent successfully to {chat_id}")
                    else:
                        error_text = await response.text()
                        logging.error(
                            f"Failed to send Telegram message to {chat_id}: {error_text}"
                        )
                        # If user blocked the bot, remove them from subscriptions
                        if "bot was blocked by the user" in error_text:
                            self.subscribed_users.pop(str(chat_id), None)
                            self.save_subscriptions()
        except Exception as e:
            logging.error(f"Error sending Telegram message to {chat_id}: {e}")

    def save_sonde_data(self, sonde_data, event_type):
        """Save sonde data to a log file in the sondes folder"""
        try:
            serial = sonde_data.get("serial", "unknown")
            # Sanitize filename
            safe_serial = "".join(
                c for c in serial if c.isalnum() or c in (" ", "-", "_")
            ).rstrip()
            filename = f"{safe_serial}.log"
            filepath = os.path.join(self.sondes_dir, filename)

            # Format the log entry
            log_entry = {
                "timestamp": datetime.utcnow().isoformat(),
                "event_type": event_type,
                "data": sonde_data,
            }

            # Append to the file
            with open(filepath, "a") as f:
                f.write(json.dumps(log_entry) + "\n")

            logging.info(f"Saved data for sonde {serial} to {filepath}")
        except Exception as e:
            logging.error(f"Error saving sonde data: {e}")

    def should_send_notification(self, serial, event_type):
        """Check if we should send notification based on timing rules"""
        current_time = time.time()

        if event_type == "initial":
            # Always send initial detection
            return True

        elif event_type == "update":
            # Check if enough time has passed since last update
            last_time = self.last_notification_time.get(serial, 0)
            update_interval = self.notification_settings["update_interval_minutes"] * 60
            return current_time - last_time >= update_interval

        elif event_type == "landing":
            # Always send landing alerts
            return True

        return False

    def process_sonde_data(self, sonde_data):
        """Process incoming radiosonde data"""
        try:
            serial = sonde_data.get("serial")
            if not serial:
                return

            lat = sonde_data.get("lat")
            lon = sonde_data.get("lon")
            alt = sonde_data.get("alt")

            if lat is None or lon is None:
                return

            # Check if within radius and altitude
            within_radius, distance = self.is_within_radius(lat, lon)
            within_altitude = self.is_within_altitude(alt)

            if within_radius and within_altitude:
                # Determine event type
                if serial not in self.detected_sonde:
                    event_type = "initial"
                    self.detected_sonde[serial] = {
                        "first_detected": time.time(),
                        "last_position": (lat, lon),
                        "last_altitude": alt,
                    }
                elif alt < 100:  # Considered landing
                    event_type = "landing"
                else:
                    event_type = "update"

                # Save the data to history
                self.save_sonde_data(sonde_data, event_type)

                # Check if we should send notification
                if self.should_send_notification(serial, event_type):
                    message = self.format_telegram_message(
                        sonde_data, distance, event_type
                    )
                    # Send to all subscribed users
                    asyncio.create_task(self.send_telegram_message(message))

                    # Update last notification time
                    self.last_notification_time[serial] = time.time()

                    # Update tracking data
                    if event_type == "update":
                        self.detected_sonde[serial]["last_position"] = (lat, lon)
                        self.detected_sonde[serial]["last_altitude"] = alt

            # Clean up old entries (sondes that left the area)
            self.cleanup_old_entries()

        except Exception as e:
            logging.error(f"Error processing sonde data: {e}")

    def cleanup_old_entries(self):
        """Remove old entries from tracking dictionaries"""
        current_time = time.time()
        # Remove entries older than 24 hours
        old_serials = [
            serial
            for serial, data in self.detected_sonde.items()
            if current_time - data["first_detected"] > 86400
        ]

        for serial in old_serials:
            self.detected_sonde.pop(serial, None)
            self.last_notification_time.pop(serial, None)

    async def get_telegram_updates(self):
        """Check for Telegram commands"""
        url = f"https://api.telegram.org/bot{self.telegram_config['bot_token']}/getUpdates"

        params = {"timeout": 30, "offset": self.last_update_id + 1}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data["ok"] and data["result"]:
                            for update in data["result"]:
                                self.last_update_id = update["update_id"]
                                if "message" in update and "text" in update["message"]:
                                    await self.handle_command(update["message"])
        except Exception as e:
            logging.error(f"Error getting Telegram updates: {e}")

    async def handle_command(self, message):
        """Handle Telegram bot commands"""
        chat_id = message["chat"]["id"]
        text = message["text"].strip()

        # Check if user is authorized (either in config or subscribed)
        is_authorized = (
            str(chat_id) == str(self.telegram_config.get("admin_chat_id", ""))
            or str(chat_id) in self.subscribed_users
        )

        if text.startswith("/"):
            command = text.split()[0].lower()

            if command == "/start":
                await self.cmd_start(
                    chat_id, message.get("from", {}).get("first_name", "User")
                )
            elif command == "/stop":
                await self.cmd_stop(chat_id)
            elif not is_authorized:
                await self.send_telegram_message(
                    "❌ You are not authorized to use this bot. Use /start to subscribe.",
                    chat_id,
                )
            elif command == "/status":
                await self.cmd_status(chat_id)
            elif command == "/list":
                await self.cmd_list(chat_id)
            elif command == "/history":
                await self.cmd_history(chat_id, text)
            elif command == "/help":
                await self.cmd_help(chat_id)
            elif command == "/subscribers" and str(chat_id) == str(
                self.telegram_config.get("admin_chat_id", "")
            ):
                await self.cmd_subscribers(chat_id)
            else:
                await self.send_telegram_message(
                    "❌ Unknown command. Use /help for available commands.", chat_id
                )

    async def cmd_start(self, chat_id, first_name):
        """Subscribe user to notifications"""
        chat_id_str = str(chat_id)
        if chat_id_str not in self.subscribed_users:
            self.subscribed_users[chat_id_str] = {
                "name": first_name,
                "subscribed_at": datetime.now().isoformat(),
            }
            self.save_subscriptions()
            logging.info(f"User {first_name} ({chat_id}) subscribed to notifications")

        message = f"👋 Welcome, {first_name}!\n\n"
        message += "✅ You are now subscribed to radiosonde notifications.\n\n"
        message += (
            "You will receive alerts when radiosondes enter the monitoring area:\n"
        )
        message += f"• Center: {self.monitoring_config['target_latitude']}, {self.monitoring_config['target_longitude']}\n"
        message += f"• Radius: {self.monitoring_config['radius_km']} km\n"
        message += f"• Altitude: {self.monitoring_config['min_altitude_m']} - {self.monitoring_config['max_altitude_m']} m\n\n"
        message += "Use /help to see all available commands.\n"
        message += "Use /stop to unsubscribe from notifications."

        await self.send_telegram_message(message, chat_id)

    async def cmd_stop(self, chat_id):
        """Unsubscribe user from notifications"""
        chat_id_str = str(chat_id)
        if chat_id_str in self.subscribed_users:
            user_name = self.subscribed_users[chat_id_str]["name"]
            self.subscribed_users.pop(chat_id_str, None)
            self.save_subscriptions()
            logging.info(
                f"User {user_name} ({chat_id}) unsubscribed from notifications"
            )
            await self.send_telegram_message(
                "❌ You have been unsubscribed from radiosonde notifications.", chat_id
            )
        else:
            await self.send_telegram_message(
                "ℹ️ You are not currently subscribed to notifications.", chat_id
            )

    async def cmd_status(self, chat_id):
        """Send current status of the monitor"""
        active_sondes = len(self.detected_sonde)
        subscribers = len(self.subscribed_users)
        message = f"📊 *Radiosonde Monitor Status*\n\n"
        message += f"• Active sondes being tracked: {active_sondes}\n"
        message += f"• Subscribed users: {subscribers}\n"
        message += f"• Monitoring center: {self.monitoring_config['target_latitude']}, {self.monitoring_config['target_longitude']}\n"
        message += f"• Monitoring radius: {self.monitoring_config['radius_km']} km\n"
        message += f"• Altitude range: {self.monitoring_config['min_altitude_m']} - {self.monitoring_config['max_altitude_m']} m\n"

        await self.send_telegram_message(message, chat_id)

    async def cmd_list(self, chat_id):
        """List all currently tracked sondes"""
        if not self.detected_sonde:
            await self.send_telegram_message(
                "No active sondes being tracked currently.", chat_id
            )
            return

        message = "📋 *Currently Tracked Sondes*\n\n"
        for serial, data in self.detected_sonde.items():
            lat, lon = data["last_position"]
            alt = data["last_altitude"]
            age = (time.time() - data["first_detected"]) / 60  # minutes

            message += f"• `{serial}`\n"
            message += f"  Position: {lat:.4f}, {lon:.4f}\n"
            message += f"  Altitude: {alt:.0f} m\n"
            message += f"  Tracking for: {age:.1f} minutes\n\n"

        await self.send_telegram_message(message, chat_id)

    async def cmd_history(self, chat_id, text):
        """Show history for a specific sonde"""
        parts = text.split()
        if len(parts) < 2:
            await self.send_telegram_message(
                "Please specify a sonde serial. Usage: /history <serial>", chat_id
            )
            return

        serial = parts[1]
        safe_serial = "".join(
            c for c in serial if c.isalnum() or c in (" ", "-", "_")
        ).rstrip()
        filename = f"{safe_serial}.log"
        filepath = os.path.join(self.sondes_dir, filename)

        if not os.path.exists(filepath):
            await self.send_telegram_message(
                f"No history found for sonde `{serial}`", chat_id
            )
            return

        try:
            with open(filepath, "r") as f:
                lines = f.readlines()

            if not lines:
                await self.send_telegram_message(
                    f"No history data for sonde `{serial}`", chat_id
                )
                return

            # Count events by type
            events = {"initial": 0, "update": 0, "landing": 0}
            last_event = None

            for line in lines:
                data = json.loads(line.strip())
                event_type = data.get("event_type", "unknown")
                if event_type in events:
                    events[event_type] += 1
                last_event = data

            message = f"📜 *History for Sonde* `{serial}`\n\n"
            message += f"• Total records: {len(lines)}\n"
            message += f"• Detections: {events['initial']}\n"
            message += f"• Updates: {events['update']}\n"
            message += f"• Landings: {events['landing']}\n\n"

            if last_event:
                last_time = datetime.fromisoformat(last_event["timestamp"])
                message += (
                    f"• Last event: {last_time.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
                )
                message += f"• Last event type: {last_event['event_type']}\n"

            await self.send_telegram_message(message, chat_id)

        except Exception as e:
            logging.error(f"Error reading history file: {e}")
            await self.send_telegram_message(
                f"Error reading history for sonde `{serial}`", chat_id
            )

    async def cmd_help(self, chat_id):
        """Show help message with available commands"""
        message = "🤖 *Radiosonde Notifier Bot Help*\n\n"
        message += "Available commands:\n"
        message += "• /start - Subscribe to radiosonde notifications\n"
        message += "• /stop - Unsubscribe from notifications\n"
        message += "• /status - Show current monitoring status\n"
        message += "• /list - List all currently tracked sondes\n"
        message += "• /history <serial> - Show history for a specific sonde\n"
        message += "• /help - Show this help message\n\n"
        message += (
            "The bot automatically alerts when radiosondes enter the monitoring area."
        )

        await self.send_telegram_message(message, chat_id)

    async def cmd_subscribers(self, chat_id):
        """Show list of subscribers (admin only)"""
        if not self.subscribed_users:
            await self.send_telegram_message("No subscribers yet.", chat_id)
            return

        message = "👥 *Subscribed Users*\n\n"
        for user_id, user_data in self.subscribed_users.items():
            subscribed_at = datetime.fromisoformat(user_data["subscribed_at"])
            message += f"• {user_data['name']} (ID: {user_id})\n"
            message += (
                f"  Subscribed: {subscribed_at.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            )

        await self.send_telegram_message(message, chat_id)

    async def run(self):
        """Main execution loop"""
        logging.info("Starting Radiosonde Notifier...")
        logging.info(
            f"Monitoring area: {self.monitoring_config['target_latitude']}, {self.monitoring_config['target_longitude']}"
        )
        logging.info(f"Radius: {self.monitoring_config['radius_km']} km")
        logging.info(f"Subscribed users: {len(self.subscribed_users)}")

        # Create a callback function for SondeHub messages
        def on_message(message):
            self.process_sonde_data(message)

        # Start the SondeHub client
        self.sondehub_stream = sondehub.Stream(on_message=on_message)
        logging.info("Connected to SondeHub. Monitoring for radiosondes...")

        try:
            # Send startup message to admin
            admin_chat_id = self.telegram_config.get("admin_chat_id")
            if admin_chat_id:
                startup_msg = "✅ Radiosonde Notifier started successfully!\n"
                startup_msg += f"Monitoring area: {self.monitoring_config['target_latitude']}, {self.monitoring_config['target_longitude']}\n"
                startup_msg += f"Radius: {self.monitoring_config['radius_km']} km\n"
                startup_msg += f"Subscribed users: {len(self.subscribed_users)}"
                await self.send_telegram_message(startup_msg, admin_chat_id)

            # Main loop with both SondeHub monitoring and Telegram command handling
            while True:
                # Check for Telegram commands
                await self.get_telegram_updates()

                # Sleep for a bit before checking again
                await asyncio.sleep(5)

        except KeyboardInterrupt:
            logging.info("Shutting down...")
        except Exception as e:
            logging.error(f"Error in main loop: {e}")
        finally:
            if self.sondehub_stream:
                self.sondehub_stream.close()


async def main():
    """Main function"""
    try:
        notifier = RadiosondeNotifier()
        await notifier.run()
    except Exception as e:
        logging.error(f"Failed to start notifier: {e}")


if __name__ == "__main__":
    asyncio.run(main())
