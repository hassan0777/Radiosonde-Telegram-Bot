import json
import logging
import time
import math
import asyncio
import os
import threading
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
        self.sondehub_connected = False
        self.sondehub_reconnect_attempts = 0
        self.max_reconnect_attempts = 10
        self.reconnect_delay = 30  # seconds

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

        # Create a queue for sonde data processing
        self.sonde_queue = asyncio.Queue()
        self.processing_task = None

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

    def is_descending(self, serial, current_altitude, current_time, sonde_timestamp):
        """Check if the radiosonde is descending using proper altitude comparison"""
        if serial not in self.detected_sonde:
            # First detection, can't determine descent yet
            return False

        previous_data = self.detected_sonde[serial]
        previous_altitude = previous_data.get("last_altitude")
        previous_time = previous_data.get(
            "last_sonde_time"
        )  # Use sonde timestamp, not system time

        if previous_altitude is None or previous_time is None:
            return False

        try:
            # Convert timestamps to datetime objects for proper comparison
            if isinstance(previous_time, str):
                prev_dt = datetime.fromisoformat(previous_time.replace("Z", "+00:00"))
            else:
                prev_dt = previous_time

            if isinstance(sonde_timestamp, str):
                current_dt = datetime.fromisoformat(
                    sonde_timestamp.replace("Z", "+00:00")
                )
            else:
                current_dt = sonde_timestamp

            # Calculate time difference in seconds
            time_diff = (current_dt - prev_dt).total_seconds()

            # Only check if we have recent data (within 10 minutes)
            if time_diff > 600 or time_diff <= 0:
                return False

            # Calculate altitude change (positive means descending)
            altitude_diff = previous_altitude - current_altitude

            # Calculate descent rate (meters per second)
            descent_rate = altitude_diff / time_diff

            # Consider descending if:
            # 1. Altitude decreased by at least 50 meters AND
            # 2. Descent rate > 1.0 m/s (typical descent rate for falling sondes)
            # 3. Time difference is reasonable (not too long)
            return altitude_diff >= 50 and descent_rate > 1.0 and time_diff < 300

        except (ValueError, TypeError) as e:
            logging.warning(f"Error parsing timestamps for descent detection: {e}")
            return False

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
        message += f"*Distanta:* {distance_km:.1f} km de centrul ariei\n"
        message += f"*Pozitie:* {lat:.4f}°, {lon:.4f}°\n"
        message += f"*Altitudine:* {alt:.0f} m\n"
        message += f"*Viteza orizontala:* {velocity_h:.1f} m/s\n"
        message += f"*Viteza verticala:* {velocity_v:.1f} m/s\n"
        message += f"*Frecventa:* {frequency}\n"
        message += f"*Ultima actualizare:* {time_str}\n\n"

        # Add status information
        if event_type == "initial":
            message += "📉 *Sonda detectata!*\n\n"
        elif event_type == "update":
            message += "📉 *Sonda este in cadere!*\n\n"
        elif event_type == "landing":
            message += "🪂 *Sonda a aterizat!*\n\n"

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
                async with session.post(url, json=payload, timeout=30) as response:
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
        except asyncio.TimeoutError:
            logging.warning(f"Timeout la trimiterea mesajului Telegram catre {chat_id}")
        except aiohttp.ClientError as e:
            logging.warning(f"Eroare de retea la trimiterea mesajului Telegram: {e}")
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

    def on_message(self, message):
        """Callback for MQTT messages - accepts proper parameters for sondehub"""
        try:
            # Put the data in the queue for async processing
            asyncio.run_coroutine_threadsafe(self.sonde_queue.put(message), self.loop)
        except Exception as e:
            logging.error(f"Eroare la adaugarea datelor in coada: {e}")

    async def process_sonde_data(self, sonde_data):
        """Process incoming radiosonde data with improved descent detection"""
        try:
            serial = sonde_data.get("serial")
            if not serial:
                return

            # Skip invalid data with encoding issues
            if (
                "rs41_subframe" in sonde_data
                and len(sonde_data["rs41_subframe"]) > 1000
            ):
                logging.debug(
                    f"Skipping sonde data with large rs41_subframe for {serial}"
                )
                return

            lat = sonde_data.get("lat")
            lon = sonde_data.get("lon")
            alt = sonde_data.get("alt")
            sonde_time = sonde_data.get("datetime")
            velocity_v = sonde_data.get(
                "vel_v", 0
            )  # Use vertical velocity if available

            if lat is None or lon is None or alt is None or sonde_time is None:
                return

            # Check if within radius and altitude
            within_radius, distance = self.is_within_radius(lat, lon)
            within_altitude = self.is_within_altitude(alt)

            if within_radius and within_altitude:
                # Check if the sonde is descending using multiple methods
                is_descending = False

                # Method 1: Use vertical velocity if available (most reliable)
                if velocity_v < -1.0:  # Negative vertical velocity means descending
                    is_descending = True
                    logging.info(
                        f"Sonde {serial} descending based on vertical velocity: {velocity_v:.1f} m/s"
                    )

                # Method 2: Use altitude comparison if vertical velocity not available
                elif not is_descending and serial in self.detected_sonde:
                    is_descending = self.is_descending(
                        serial, alt, time.time(), sonde_time
                    )
                    if is_descending:
                        logging.info(
                            f"Sonde {serial} descending based on altitude comparison"
                        )

                # Determine event type
                event_type = None

                if serial not in self.detected_sonde:
                    # First detection in target area
                    event_type = "initial"
                    self.detected_sonde[serial] = {
                        "first_detected": time.time(),
                        "last_position": (lat, lon),
                        "last_altitude": alt,
                        "last_update_time": time.time(),
                        "last_sonde_time": sonde_time,  # Store sonde timestamp
                        "is_descending": is_descending,
                        "vertical_velocity": velocity_v,
                    }
                elif alt < 100:  # Considered landing
                    event_type = "landing"
                    is_descending = True  # Force descending for landing
                elif is_descending:
                    event_type = "update"
                else:
                    # Sonde is in area but not descending, update tracking but don't notify
                    if serial in self.detected_sonde:
                        self.detected_sonde[serial].update(
                            {
                                "last_position": (lat, lon),
                                "last_altitude": alt,
                                "last_update_time": time.time(),
                                "last_sonde_time": sonde_time,
                                "is_descending": is_descending,
                                "vertical_velocity": velocity_v,
                            }
                        )
                    return

                # Only send notifications for descending or landing sondes
                if event_type in ["initial", "update", "landing"]:
                    # Save the data to history
                    self.save_sonde_data(sonde_data, event_type)

                    # Check if we should send notification
                    if self.should_send_notification(serial, event_type):
                        message = self.format_telegram_message(
                            sonde_data, distance, event_type
                        )
                        # Send to all subscribed users
                        await self.send_telegram_message(message)

                        # Update last notification time
                        self.last_notification_time[serial] = time.time()

                # Update tracking data
                if serial in self.detected_sonde:
                    self.detected_sonde[serial].update(
                        {
                            "last_position": (lat, lon),
                            "last_altitude": alt,
                            "last_update_time": time.time(),
                            "last_sonde_time": sonde_time,
                            "is_descending": is_descending,
                            "vertical_velocity": velocity_v,
                        }
                    )

            # Clean up old entries (sondes that left the area)
            self.cleanup_old_entries()

        except Exception as e:
            logging.error(f"Eroare la procesarea datelor sondei: {e}")
            import traceback

            logging.error(traceback.format_exc())

    async def sonde_processor(self):
        """Process sonde data from the queue"""
        while True:
            try:
                sonde_data = await self.sonde_queue.get()
                await self.process_sonde_data(sonde_data)
                self.sonde_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"Eroare in procesorul de date sonde: {e}")

    def analyze_sonde_trend(self, serial, current_data):
        """Analyze sonde data trend over multiple points for better descent detection"""
        if serial not in self.detected_sonde:
            return False

        history = self.detected_sonde[serial].get("altitude_history", [])

        # Store current altitude with timestamp
        current_alt = current_data.get("alt")
        current_time = current_data.get("datetime")

        if current_alt is None or current_time is None:
            return False

        # Add to history (keep last 10 points)
        history.append(
            {
                "altitude": current_alt,
                "timestamp": current_time,
                "time_received": time.time(),
            }
        )

        if len(history) > 10:
            history.pop(0)

        self.detected_sonde[serial]["altitude_history"] = history

        # Need at least 3 points to analyze trend
        if len(history) < 3:
            return False

        # Calculate average descent rate over history
        total_descent = 0
        total_time = 0

        for i in range(1, len(history)):
            try:
                alt_diff = history[i - 1]["altitude"] - history[i]["altitude"]
                time_diff = (
                    datetime.fromisoformat(
                        history[i]["timestamp"].replace("Z", "+00:00")
                    )
                    - datetime.fromisoformat(
                        history[i - 1]["timestamp"].replace("Z", "+00:00")
                    )
                ).total_seconds()

                if time_diff > 0:
                    total_descent += alt_diff
                    total_time += time_diff
            except (ValueError, TypeError):
                continue

        if total_time > 0:
            avg_descent_rate = total_descent / total_time
            # Consider descending if average rate > 0.5 m/s
            return avg_descent_rate > 0.5

        return False

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
                async with session.get(url, params=params, timeout=30) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data["ok"] and data["result"]:
                            for update in data["result"]:
                                self.last_update_id = update["update_id"]
                                if "message" in update and "text" in update["message"]:
                                    await self.handle_command(update["message"])
        except asyncio.TimeoutError:
            logging.warning("Timeout la preluarea actualizarilor Telegram")
        except aiohttp.ClientError as e:
            logging.warning(
                f"Eroare de retea la preluarea actualizarilor Telegram: {e}"
            )
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
        message += "Vei primi alerte cand radiosondele intra in zona de monitorizare SI sunt in cadere:\n"
        message += f"• Centru: {self.monitoring_config['target_latitude']}, {self.monitoring_config['target_longitude']}\n"
        message += f"• Raza: {self.monitoring_config['radius_km']} km\n"
        message += f"• Altitudine: {self.monitoring_config['min_altitude_m']} - {self.monitoring_config['max_altitude_m']} m\n"
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
        sondehub_status = "✅ Conectat" if self.sondehub_connected else "❌ Deconectat"

        message = f"📊 *Status Monitor Radiosonde*\n\n"
        message += f"• Sonde active urmarite: {active_sondes}\n"
        message += f"• Utilizatori abonati: {subscribers}\n"
        message += f"• Status SondeHub: {sondehub_status}\n"
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
            descending = data.get("is_descending", False)

            message += f"• `{serial}`\n"
            message += f"  Pozitie: {lat:.4f}, {lon:.4f}\n"
            message += f"  Altitudine: {alt:.0f} m\n"
            message += f"  Status: {'📉 In cadere' if descending else '➡️ Stabil'}\n"
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
                f"Nu s-a gasistoric pentru sonda `{serial}`", chat_id
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
                try:
                    data = json.loads(line.strip())
                    event_type = data.get("event_type", "necunoscut")
                    if event_type in events:
                        events[event_type] += 1
                    last_event = data
                except json.JSONDecodeError:
                    continue  # Skip invalid JSON lines

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
        message += "⚠️ *Atentie:* Botul va alerta doar cand radiosondele sunt in cadere si in zona de monitorizare."

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

    def on_connect(self, client, userdata, flags, rc):
        """Callback for MQTT connection - accepts proper parameters"""
        self.sondehub_connected = True
        self.sondehub_reconnect_attempts = 0
        logging.info("Conectat cu succes la SondeHub")

    def on_disconnect(self, client, userdata, rc):
        """Callback for MQTT disconnection - accepts proper parameters"""
        self.sondehub_connected = False
        logging.warning("Deconectat de la SondeHub")

    async def connect_to_sondehub(self):
        """Connect to SondeHub with retry logic"""
        try:
            # Create a new Stream instance with the correct callback signatures
            self.sondehub_stream = sondehub.Stream(
                on_message=self.on_message,
                on_connect=self.on_connect,
                on_disconnect=self.on_disconnect,
            )
            self.sondehub_connected = True
            logging.info("Conectat la SondeHub. Se monitorizeaza radiosondele...")
            return True
        except Exception as e:
            logging.error(f"Eroare la conectarea la SondeHub: {e}")
            self.sondehub_connected = False
            return False

    async def reconnect_sondehub(self):
        """Reconnect to SondeHub with exponential backoff"""
        if self.sondehub_reconnect_attempts >= self.max_reconnect_attempts:
            logging.error("Numar maxim de incercari de reconectare la SondeHub atins")
            return False

        delay = self.reconnect_delay * (2**self.sondehub_reconnect_attempts)
        self.sondehub_reconnect_attempts += 1

        logging.warning(
            f"Reincercare conectare la SondeHub in {delay} secunde (incercarea {self.sondehub_reconnect_attempts}/{self.max_reconnect_attempts})"
        )

        await asyncio.sleep(delay)
        return await self.connect_to_sondehub()

    async def run(self):
        """Main execution loop"""
        logging.info("Pornire Monitor Radiosonde...")
        logging.info(
            f"Zona de monitorizare: {self.monitoring_config['target_latitude']}, {self.monitoring_config['target_longitude']}"
        )
        logging.info(f"Raza: {self.monitoring_config['radius_km']} km")
        logging.info(f"Utilizatori abonati: {len(self.subscribed_users)}")

        # Store the event loop reference
        self.loop = asyncio.get_event_loop()

        # Start the sonde processor task
        self.processing_task = asyncio.create_task(self.sonde_processor())

        # Connect to SondeHub
        sondehub_connected = await self.connect_to_sondehub()
        if not sondehub_connected:
            logging.warning("Nu s-a putut conecta initial la SondeHub")

        try:
            # Send startup message to admin
            admin_chat_id = self.telegram_config.get("admin_chat_id")
            if admin_chat_id:
                startup_msg = "✅ Monitorul de Radiosonde a pornit cu succes!\n"
                startup_msg += f"Zona de monitorizare: {self.monitoring_config['target_latitude']}, {self.monitoring_config['target_longitude']}\n"
                startup_msg += f"Raza: {self.monitoring_config['radius_km']} km\n"
                startup_msg += f"Utilizatori abonati: {len(self.subscribed_users)}\n"
                startup_msg += f"Status SondeHub: {'✅ Conectat' if sondehub_connected else '❌ Deconectat'}"
                await self.send_telegram_message(startup_msg, admin_chat_id)

            # Main loop with both SondeHub monitoring and Telegram command handling
            while True:
                # Check for Telegram commands
                await self.get_telegram_updates()

                # Check SondeHub connection status and reconnect if needed
                if not self.sondehub_connected:
                    await self.reconnect_sondehub()

                # Sleep for a bit before checking again
                await asyncio.sleep(5)

        except KeyboardInterrupt:
            logging.info("Se inchide...")
        except Exception as e:
            logging.error(f"Eroare in bucla principala: {e}")
        finally:
            # Cancel the processing task
            if self.processing_task:
                self.processing_task.cancel()
                try:
                    await self.processing_task
                except asyncio.CancelledError:
                    pass

            if self.sondehub_stream:
                self.sondehub_stream.disconnect()


async def main():
    """Main function"""
    try:
        notifier = RadiosondeNotifier()
        await notifier.run()
    except Exception as e:
        logging.error(f"Nu s-a putut porni monitorul: {e}")


if __name__ == "__main__":
    asyncio.run(main())
