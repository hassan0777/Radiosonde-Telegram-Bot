import json
import logging
import time
import math
import asyncio
import os
from datetime import datetime, timedelta
import aiohttp
import sondehub

# Configure logging with proper encoding
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
        """Set up file logging to the bot directory with proper encoding"""
        # Remove any existing file handlers
        for handler in logging.root.handlers[:]:
            if isinstance(handler, logging.FileHandler):
                logging.root.removeHandler(handler)

        # Add file handler to bot directory with UTF-8 encoding
        log_file = os.path.join(self.bot_dir, "radiosonde_notifier.log")
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        )
        logging.root.addHandler(file_handler)

        # Also update the stream handler to handle Unicode properly
        for handler in logging.root.handlers:
            if isinstance(handler, logging.StreamHandler):
                # Replace with a StreamHandler that uses UTF-8
                logging.root.removeHandler(handler)
                break

        # Create a stream handler that can handle Unicode
        class UnicodeStreamHandler(logging.StreamHandler):
            def emit(self, record):
                try:
                    msg = self.format(record)
                    # Encode to UTF-8 and then decode with replace to handle any encoding issues
                    msg = msg.encode("utf-8", "replace").decode("utf-8", "replace")
                    stream = self.stream
                    stream.write(msg + self.terminator)
                    self.flush()
                except Exception:
                    self.handleError(record)

        stream_handler = UnicodeStreamHandler()
        stream_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        )
        logging.root.addHandler(stream_handler)

    def load_config(self, config_file):
        """Load configuration from JSON file"""
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            logging.error(f"Fisierul de configurare {config_file} nu a fost gasit!")
            raise
        except json.JSONDecodeError:
            logging.error(f"JSON invalid in fisierul de configurare {config_file}!")
            raise

    def load_subscriptions(self):
        """Load user subscriptions from file"""
        try:
            if os.path.exists(self.subscriptions_file):
                with open(self.subscriptions_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            return {}
        except Exception as e:
            logging.error(f"Eroare la incarcarea abonarilor: {e}")
            return {}

    def save_subscriptions(self):
        """Save user subscriptions to file"""
        try:
            with open(self.subscriptions_file, "w", encoding="utf-8") as f:
                json.dump(self.subscribed_users, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logging.error(f"Eroare la salvarea abonarilor: {e}")

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
        serial = sonde_data.get("serial", "Necunoscut")
        lat = sonde_data.get("lat", 0)
        lon = sonde_data.get("lon", 0)
        alt = sonde_data.get("alt", 0)
        velocity_h = sonde_data.get("vel_h", 0)
        velocity_v = sonde_data.get("vel_v", 0)
        frequency = sonde_data.get("frequency", "Necunoscuta")
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

        message = f"{emoji} *Alerta Radiosonda* {emoji}\n\n"
        message += f"*Eveniment:* {'Detectare' if event_type == 'initial' else 'Actualizare' if event_type == 'update' else 'Aterizare'}\n"
        message += f"*Serial:* `{serial}`\n"
        message += f"*Distanta:* {distance_km:.1f} km de tinta\n"
        message += f"*Pozitie:* {lat:.4f}°, {lon:.4f}°\n"
        message += f"*Altitudine:* {alt:.0f} m\n"
        message += f"*Viteza orizontala:* {velocity_h:.1f} m/s\n"
        message += f"*Viteza verticala:* {velocity_v:.1f} m/s\n"
        message += f"*Frecventa:* {frequency}\n"
        message += f"*Ultima actualizare:* {time_str}\n\n"

        # Add Google Maps link
        maps_link = f"https://maps.google.com/?q={lat},{lon}"
        message += f"📍 [Vezi pe Google Maps]({maps_link})"

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
                        logging.info(f"Mesaj Telegram trimis cu succes catre {chat_id}")
                    else:
                        error_text = await response.text()
                        logging.error(
                            f"Nu s-a putut trimite mesajul Telegram catre {chat_id}: {error_text}"
                        )
                        # If user blocked the bot, remove them from subscriptions
                        if "bot was blocked by the user" in error_text:
                            self.subscribed_users.pop(str(chat_id), None)
                            self.save_subscriptions()
        except Exception as e:
            logging.error(
                f"Eroare la trimiterea mesajului Telegram catre {chat_id}: {e}"
            )

    def save_sonde_data(self, sonde_data, event_type):
        """Save sonde data to a log file in the sondes folder"""
        try:
            serial = sonde_data.get("serial", "necunoscut")
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
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

            logging.info(f"Date salvate pentru sonda {serial} in {filepath}")
        except Exception as e:
            logging.error(f"Eroare la salvarea datelor sondei: {e}")

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
            logging.error(f"Eroare la procesarea datelor sondei: {e}")

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
            logging.error(f"Eroare la preluarea actualizarilor Telegram: {e}")

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
                    chat_id, message.get("from", {}).get("first_name", "Utilizator")
                )
            elif command == "/stop":
                await self.cmd_stop(chat_id)
            elif not is_authorized:
                await self.send_telegram_message(
                    "❌ Nu esti autorizat sa folosesti acest bot. Foloseste /start pentru a te abona.",
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
                    "❌ Comanda necunoscuta. Foloseste /help pentru comenzi disponibile.",
                    chat_id,
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
            logging.info(
                f"Utilizatorul {first_name} ({chat_id}) s-a abonat la notificari"
            )

        message = f"👋 Bun venit, {first_name}!\n\n"
        message += "✅ Acum esti abonat la notificarile pentru radiosonde.\n\n"
        message += "Vei primi alerte cand radiosondele intra in zona de monitorizare:\n"
        message += f"• Centru: {self.monitoring_config['target_latitude']}, {self.monitoring_config['target_longitude']}\n"
        message += f"• Raza: {self.monitoring_config['radius_km']} km\n"
        message += f"• Altitudine: {self.monitoring_config['min_altitude_m']} - {self.monitoring_config['max_altitude_m']} m\n\n"
        message += "Foloseste /help pentru a vedea toate comenzile disponibile.\n"
        message += "Foloseste /stop pentru a te dezabona de la notificari."

        await self.send_telegram_message(message, chat_id)

    async def cmd_stop(self, chat_id):
        """Unsubscribe user from notifications"""
        chat_id_str = str(chat_id)
        if chat_id_str in self.subscribed_users:
            user_name = self.subscribed_users[chat_id_str]["name"]
            self.subscribed_users.pop(chat_id_str, None)
            self.save_subscriptions()
            logging.info(
                f"Utilizatorul {user_name} ({chat_id}) s-a dezabonat de la notificari"
            )
            await self.send_telegram_message(
                "❌ Ai fost dezabonat de la notificarile pentru radiosonde.", chat_id
            )
        else:
            await self.send_telegram_message(
                "ℹ️ Nu esti abonat in prezent la notificari.", chat_id
            )

    async def cmd_status(self, chat_id):
        """Send current status of the monitor"""
        active_sondes = len(self.detected_sonde)
        subscribers = len(self.subscribed_users)
        message = f"📊 *Status Monitor Radiosonde*\n\n"
        message += f"• Sonde active urmarite: {active_sondes}\n"
        message += f"• Utilizatori abonati: {subscribers}\n"
        message += f"• Centru monitorizare: {self.monitoring_config['target_latitude']}, {self.monitoring_config['target_longitude']}\n"
        message += f"• Raza monitorizare: {self.monitoring_config['radius_km']} km\n"
        message += f"• Interval altitudine: {self.monitoring_config['min_altitude_m']} - {self.monitoring_config['max_altitude_m']} m\n"

        await self.send_telegram_message(message, chat_id)

    async def cmd_list(self, chat_id):
        """List all currently tracked sondes"""
        if not self.detected_sonde:
            await self.send_telegram_message(
                "In prezent nu sunt sonde active urmarite.", chat_id
            )
            return

        message = "📋 *Sonide Urmarite In Prezent*\n\n"
        for serial, data in self.detected_sonde.items():
            lat, lon = data["last_position"]
            alt = data["last_altitude"]
            age = (time.time() - data["first_detected"]) / 60  # minutes

            message += f"• `{serial}`\n"
            message += f"  Pozitie: {lat:.4f}, {lon:.4f}\n"
            message += f"  Altitudine: {alt:.0f} m\n"
            message += f"  Urmarit de: {age:.1f} minute\n\n"

        await self.send_telegram_message(message, chat_id)

    async def cmd_history(self, chat_id, text):
        """Show history for a specific sonde"""
        parts = text.split()
        if len(parts) < 2:
            await self.send_telegram_message(
                "Te rog specifica un serial de sonda. Utilizare: /history <serial>",
                chat_id,
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
                f"Nu s-a gasit istoric pentru sonda `{serial}`", chat_id
            )
            return

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                lines = f.readlines()

            if not lines:
                await self.send_telegram_message(
                    f"Nu exista date de istoric pentru sonda `{serial}`", chat_id
                )
                return

            # Count events by type
            events = {"initial": 0, "update": 0, "landing": 0}
            last_event = None

            for line in lines:
                data = json.loads(line.strip())
                event_type = data.get("event_type", "necunoscut")
                if event_type in events:
                    events[event_type] += 1
                last_event = data

            message = f"📜 *Istoric pentru Sonda* `{serial}`\n\n"
            message += f"• Total inregistrari: {len(lines)}\n"
            message += f"• Detectari: {events['initial']}\n"
            message += f"• Actualizari: {events['update']}\n"
            message += f"• Aterizari: {events['landing']}\n\n"

            if last_event:
                last_time = datetime.fromisoformat(last_event["timestamp"])
                message += f"• Ultimul eveniment: {last_time.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
                message += f"• Tip ultim eveniment: {last_event['event_type']}\n"

            await self.send_telegram_message(message, chat_id)

        except Exception as e:
            logging.error(f"Eroare la citirea fisierului de istoric: {e}")
            await self.send_telegram_message(
                f"Eroare la citirea istoricului pentru sonda `{serial}`", chat_id
            )

    async def cmd_help(self, chat_id):
        """Show help message with available commands"""
        message = "🤖 *Ajutor Bot Notificari Radiosonde*\n\n"
        message += "Comenzi disponibile:\n"
        message += "• /start - Aboneaza-te la notificari pentru radiosonde\n"
        message += "• /stop - Dezaboneaza-te de la notificari\n"
        message += "• /status - Afiseaza statusul curent de monitorizare\n"
        message += "• /list - Lista toate sondele urmarite in prezent\n"
        message += "• /history <serial> - Afiseaza istoricul pentru o anumita sonda\n"
        message += "• /help - Afiseaza acest mesaj de ajutor\n\n"
        message += (
            "Botul va alerta automat cand radiosondele intra in zona de monitorizare."
        )

        await self.send_telegram_message(message, chat_id)

    async def cmd_subscribers(self, chat_id):
        """Show list of subscribers (admin only)"""
        if not self.subscribed_users:
            await self.send_telegram_message("Inca nu exista abonati.", chat_id)
            return

        message = "👥 *Utilizatori Abonati*\n\n"
        for user_id, user_data in self.subscribed_users.items():
            subscribed_at = datetime.fromisoformat(user_data["subscribed_at"])
            message += f"• {user_data['name']} (ID: {user_id})\n"
            message += f"  Abonat la: {subscribed_at.strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        await self.send_telegram_message(message, chat_id)

    async def run(self):
        """Main execution loop"""
        logging.info("Pornire Monitor Radiosonde...")
        logging.info(
            f"Zona de monitorizare: {self.monitoring_config['target_latitude']}, {self.monitoring_config['target_longitude']}"
        )
        logging.info(f"Raza: {self.monitoring_config['radius_km']} km")
        logging.info(f"Utilizatori abonati: {len(self.subscribed_users)}")

        # Create a callback function for SondeHub messages
        def on_message(message):
            self.process_sonde_data(message)

        # Start the SondeHub client
        self.sondehub_stream = sondehub.Stream(on_message=on_message)
        logging.info("Conectat la SondeHub. Se monitorizeaza radiosondele...")

        try:
            # Send startup message to admin
            admin_chat_id = self.telegram_config.get("admin_chat_id")
            if admin_chat_id:
                startup_msg = "✅ Monitorul de Radiosonde a pornit cu succes!\n"
                startup_msg += f"Zona de monitorizare: {self.monitoring_config['target_latitude']}, {self.monitoring_config['target_longitude']}\n"
                startup_msg += f"Raza: {self.monitoring_config['radius_km']} km\n"
                startup_msg += f"Utilizatori abonati: {len(self.subscribed_users)}"
                await self.send_telegram_message(startup_msg, admin_chat_id)

            # Main loop with both SondeHub monitoring and Telegram command handling
            while True:
                # Check for Telegram commands
                await self.get_telegram_updates()

                # Sleep for a bit before checking again
                await asyncio.sleep(5)

        except KeyboardInterrupt:
            logging.info("Se inchide...")
        except Exception as e:
            logging.error(f"Eroare in bucla principala: {e}")
        finally:
            if self.sondehub_stream:
                self.sondehub_stream.close()


async def main():
    """Main function"""
    try:
        notifier = RadiosondeNotifier()
        await notifier.run()
    except Exception as e:
        logging.error(f"Nu s-a putut porni monitorul: {e}")


if __name__ == "__main__":
    asyncio.run(main())
