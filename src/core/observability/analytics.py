import sqlite3
import os
from datetime import datetime, date
from typing import Dict, Optional
import threading

class SessionTracker:
    """
    SQLite-based session tracking for F1 telemetry analysis.
    Tracks daily, monthly, and total session counts.
    """

    def __init__(self, db_path: str = "data/session_analytics.db"):
        self.db_path = db_path
        self.lock = threading.Lock()
        self._init_database()

    def _init_database(self):
        """Initialize the SQLite database with required tables."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # Create sessions table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date DATE NOT NULL,
                    year INTEGER NOT NULL,
                    month INTEGER NOT NULL,
                    day INTEGER NOT NULL,
                    endpoint VARCHAR(100) NOT NULL,
                    session_type VARCHAR(10) NOT NULL,
                    gp_number INTEGER,
                    race_year INTEGER,
                    driver1 VARCHAR(10),
                    driver2 VARCHAR(10),
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Create daily_stats table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS daily_stats (
                    date DATE PRIMARY KEY,
                    total_sessions INTEGER DEFAULT 0,
                    top_speed_sessions INTEGER DEFAULT 0,
                    throttle_sessions INTEGER DEFAULT 0,
                    qualifying_sessions INTEGER DEFAULT 0,
                    track_comparison_sessions INTEGER DEFAULT 0,
                    last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Create monthly_stats table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS monthly_stats (
                    year INTEGER,
                    month INTEGER,
                    total_sessions INTEGER DEFAULT 0,
                    top_speed_sessions INTEGER DEFAULT 0,
                    throttle_sessions INTEGER DEFAULT 0,
                    qualifying_sessions INTEGER DEFAULT 0,
                    track_comparison_sessions INTEGER DEFAULT 0,
                    last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (year, month)
                )
            ''')

            # Create total_stats table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS total_stats (
                    id INTEGER PRIMARY KEY,
                    total_sessions INTEGER DEFAULT 0,
                    top_speed_sessions INTEGER DEFAULT 0,
                    throttle_sessions INTEGER DEFAULT 0,
                    qualifying_sessions INTEGER DEFAULT 0,
                    track_comparison_sessions INTEGER DEFAULT 0,
                    last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Insert initial total stats record if it doesn't exist
            cursor.execute('''
                INSERT OR IGNORE INTO total_stats (id, total_sessions) 
                VALUES (1, 0)
            ''')

            conn.commit()

    def track_session(self, endpoint: str, year: int, gp: int, session: str,
                     driver1: Optional[str] = None, driver2: Optional[str] = None):
        """Track a new analyzed session."""
        with self.lock:
            today = date.today()
            now = datetime.now()

            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Insert session record
                cursor.execute('''
                    INSERT INTO sessions 
                    (date, year, month, day, endpoint, session_type, gp_number, race_year, driver1, driver2, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (today, today.year, today.month, today.day, endpoint, session, gp, year, driver1, driver2, now))

                # Update daily stats
                self._update_daily_stats(cursor, today, endpoint)

                # Update monthly stats
                self._update_monthly_stats(cursor, today.year, today.month, endpoint)

                # Update total stats
                self._update_total_stats(cursor, endpoint)

                conn.commit()

    def _update_daily_stats(self, cursor, date_obj: date, endpoint: str):
        """Update daily statistics."""
        cursor.execute('''
            INSERT OR IGNORE INTO daily_stats (date, total_sessions) 
            VALUES (?, 0)
        ''', (date_obj,))

        # Determine which column to increment
        column_map = {
            'top-speed': 'top_speed_sessions',
            'throttle-comparison': 'throttle_sessions',
            'qualifying-results': 'qualifying_sessions',
            'track-comparison-2drivers': 'track_comparison_sessions'
        }

        session_column = column_map.get(endpoint, None)

        if session_column:
            cursor.execute(f'''
                UPDATE daily_stats 
                SET total_sessions = total_sessions + 1,
                    {session_column} = {session_column} + 1,
                    last_updated = CURRENT_TIMESTAMP
                WHERE date = ?
            ''', (date_obj,))
        else:
            cursor.execute('''
                UPDATE daily_stats 
                SET total_sessions = total_sessions + 1,
                    last_updated = CURRENT_TIMESTAMP
                WHERE date = ?
            ''', (date_obj,))

    def _update_monthly_stats(self, cursor, year: int, month: int, endpoint: str):
        """Update monthly statistics."""
        cursor.execute('''
            INSERT OR IGNORE INTO monthly_stats (year, month, total_sessions) 
            VALUES (?, ?, 0)
        ''', (year, month))

        # Determine which column to increment
        column_map = {
            'top-speed': 'top_speed_sessions',
            'throttle-comparison': 'throttle_sessions',
            'qualifying-results': 'qualifying_sessions',
            'track-comparison-2drivers': 'track_comparison_sessions'
        }

        session_column = column_map.get(endpoint, None)

        if session_column:
            cursor.execute(f'''
                UPDATE monthly_stats 
                SET total_sessions = total_sessions + 1,
                    {session_column} = {session_column} + 1,
                    last_updated = CURRENT_TIMESTAMP
                WHERE year = ? AND month = ?
            ''', (year, month))
        else:
            cursor.execute('''
                UPDATE monthly_stats 
                SET total_sessions = total_sessions + 1,
                    last_updated = CURRENT_TIMESTAMP
                WHERE year = ? AND month = ?
            ''', (year, month))

    def _update_total_stats(self, cursor, endpoint: str):
        """Update total statistics."""
        # Determine which column to increment
        column_map = {
            'top-speed': 'top_speed_sessions',
            'throttle-comparison': 'throttle_sessions',
            'qualifying-results': 'qualifying_sessions',
            'track-comparison-2drivers': 'track_comparison_sessions'
        }

        session_column = column_map.get(endpoint, None)

        if session_column:
            cursor.execute(f'''
                UPDATE total_stats 
                SET total_sessions = total_sessions + 1,
                    {session_column} = {session_column} + 1,
                    last_updated = CURRENT_TIMESTAMP
                WHERE id = 1
            ''')
        else:
            cursor.execute('''
                UPDATE total_stats 
                SET total_sessions = total_sessions + 1,
                    last_updated = CURRENT_TIMESTAMP
                WHERE id = 1
            ''')

    def get_daily_stats(self, date_obj: Optional[date] = None) -> Dict:
        """Get daily statistics for a specific date or today."""
        if date_obj is None:
            date_obj = date.today()

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT total_sessions, top_speed_sessions, throttle_sessions, 
                       qualifying_sessions, track_comparison_sessions, last_updated
                FROM daily_stats 
                WHERE date = ?
            ''', (date_obj,))

            result = cursor.fetchone()
            if result:
                return {
                    'date': date_obj.isoformat(),
                    'total_sessions': result[0],
                    'top_speed_sessions': result[1],
                    'throttle_sessions': result[2],
                    'qualifying_sessions': result[3],
                    'track_comparison_sessions': result[4],
                    'last_updated': result[5]
                }
            else:
                return {
                    'date': date_obj.isoformat(),
                    'total_sessions': 0,
                    'top_speed_sessions': 0,
                    'throttle_sessions': 0,
                    'qualifying_sessions': 0,
                    'track_comparison_sessions': 0,
                    'last_updated': None
                }

    def get_monthly_stats(self, year: int, month: int) -> Dict:
        """Get monthly statistics for a specific year and month."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT total_sessions, top_speed_sessions, throttle_sessions, 
                       qualifying_sessions, track_comparison_sessions, last_updated
                FROM monthly_stats 
                WHERE year = ? AND month = ?
            ''', (year, month))

            result = cursor.fetchone()
            if result:
                return {
                    'year': year,
                    'month': month,
                    'total_sessions': result[0],
                    'top_speed_sessions': result[1],
                    'throttle_sessions': result[2],
                    'qualifying_sessions': result[3],
                    'track_comparison_sessions': result[4],
                    'last_updated': result[5]
                }
            else:
                return {
                    'year': year,
                    'month': month,
                    'total_sessions': 0,
                    'top_speed_sessions': 0,
                    'throttle_sessions': 0,
                    'qualifying_sessions': 0,
                    'track_comparison_sessions': 0,
                    'last_updated': None
                }

    def get_total_stats(self) -> Dict:
        """Get total statistics across all time."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT total_sessions, top_speed_sessions, throttle_sessions, 
                       qualifying_sessions, track_comparison_sessions, last_updated
                FROM total_stats 
                WHERE id = 1
            ''')

            result = cursor.fetchone()
            if result:
                return {
                    'total_sessions': result[0],
                    'top_speed_sessions': result[1],
                    'throttle_sessions': result[2],
                    'qualifying_sessions': result[3],
                    'track_comparison_sessions': result[4],
                    'last_updated': result[5]
                }
            else:
                return {
                    'total_sessions': 0,
                    'top_speed_sessions': 0,
                    'throttle_sessions': 0,
                    'qualifying_sessions': 0,
                    'track_comparison_sessions': 0,
                    'last_updated': None
                }

    def get_recent_sessions(self, limit: int = 10) -> list:
        """Get recent analyzed sessions."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT endpoint, session_type, gp_number, race_year, driver1, driver2, timestamp
                FROM sessions 
                ORDER BY timestamp DESC 
                LIMIT ?
            ''', (limit,))

            results = cursor.fetchall()
            return [
                {
                    'endpoint': row[0],
                    'session_type': row[1],
                    'gp_number': row[2],
                    'race_year': row[3],
                    'driver1': row[4],
                    'driver2': row[5],
                    'timestamp': row[6]
                }
                for row in results
            ]
