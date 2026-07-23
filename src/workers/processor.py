"""
Background session data processor
Monitors F1 sessions and automatically processes data after they finish
"""

import asyncio
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import fastf1
import pandas as pd
from pathlib import Path

from src.core.logging import get_logger
from src.core.config import settings
from src.workers.plot_generator import PlotDataGenerator

logger = get_logger(__name__)


class BackgroundProcessor:
    """
    Monitors F1 sessions and automatically processes data after completion
    """
    
    def __init__(self, check_interval: int = 300):
        """
        Initialize background processor
        
        Args:
            check_interval: How often to check for completed sessions (seconds)
        """
        self.check_interval = check_interval
        self.generator = PlotDataGenerator()
        self.running = False
        self.processed_sessions = set()  # Track already processed sessions
        self._load_processed_sessions()
        
    def _load_processed_sessions(self):
        """Load list of already processed sessions from MongoDB"""
        try:
            from pymongo import MongoClient
            
            client = MongoClient(
                f"mongodb://{settings.mongodb_user}:{settings.mongodb_password}@"
                f"{settings.mongodb_host}:{settings.mongodb_port}/"
            )
            db = client[settings.mongodb_database]
            
            # Get all unique session identifiers from the database
            collections = db.list_collection_names()
            for collection in collections:
                # Parse session info from collection names
                # Format: plot_type_YYYY_GP_SESSION
                if collection.count('_') >= 3:
                    parts = collection.split('_')
                    try:
                        year_idx = next(i for i, p in enumerate(parts) if p.isdigit() and len(p) == 4)
                        year = int(parts[year_idx])
                        gp = int(parts[year_idx + 1])
                        session = parts[year_idx + 2]
                        session_id = f"{year}_{gp}_{session}"
                        self.processed_sessions.add(session_id)
                    except (ValueError, IndexError, StopIteration):
                        continue
            
            logger.info(f"Loaded {len(self.processed_sessions)} previously processed sessions")
            
        except Exception as e:
            logger.warning(f"Could not load processed sessions: {e}")
            self.processed_sessions = set()
    
    def get_upcoming_sessions(self) -> list:
        """
        Get list of upcoming F1 sessions from the schedule
        
        Returns:
            List of tuples (year, gp, session, session_date)
        """
        try:
            current_year = datetime.now().year
            schedule = fastf1.get_event_schedule(current_year)
            
            upcoming = []
            now = pd.Timestamp.now(tz='UTC')  # Use timezone-aware timestamp
            
            for idx, event in schedule.iterrows():
                gp_number = idx + 1
                
                # Check all session types
                session_types = {
                    'FP1': 'Session1Date',
                    'FP2': 'Session2Date', 
                    'FP3': 'Session3Date',
                    'Q': 'Session4Date',
                    'R': 'Session5Date'
                }
                
                for session_name, date_column in session_types.items():
                    if date_column in event and not pd.isna(event[date_column]):
                        session_date = pd.to_datetime(event[date_column])
                        
                        # Make timezone-aware if needed
                        if session_date.tzinfo is None:
                            session_date = session_date.tz_localize('UTC')
                        
                        # Add sessions that finished in the last 24 hours
                        if now - pd.Timedelta(hours=24) <= session_date <= now:
                            upcoming.append((current_year, gp_number, session_name, session_date))
            
            return upcoming
            
        except Exception as e:
            logger.error(f"Error getting upcoming sessions: {e}", exc_info=True)
            return []
    
    def check_session_completed(self, year: int, gp: int, session: str) -> bool:
        """
        Check if a session has been completed and data is available
        
        Args:
            year: Year
            gp: Grand Prix round number
            session: Session type
            
        Returns:
            True if session is complete and data is available
        """
        try:
            # Try to load the session
            event = fastf1.get_session(year, gp, session)
            event.load()
            
            # Check if session has laps data (means it's complete)
            if event.laps is not None and len(event.laps) > 0:
                return True
            
            return False
            
        except Exception as e:
            logger.debug(f"Session Y{year} GP{gp} {session} not yet available: {e}")
            return False
    
    def prewarm_session_streams(self, year: int, gp, session: str, gp_name: str = None) -> None:
        """Fetch + durably cache every V2 raw stream for a session once.

        Builds a single :class:`SessionDataStore` and touches every accessor so
        the raw streams — including the multi-MB ``CarData.z`` / ``Position.z``
        telemetry — land in the durable GridFS cache (and the derived bundle in
        MongoDB) while the session is fresh. Later per-driver / per-pair requests
        then read from the warm cache instead of re-downloading.

        Fully fail-open: any warm-up error is logged and swallowed so plot
        generation still proceeds. V2 resolves by ``gp_name`` (avoids the
        pre-season-testing round-number offset), falling back to ``gp``.
        """
        from src.services.analysis.v2.session_store import SessionDataStore

        identifier = gp_name or gp
        try:
            store = SessionDataStore(year, identifier, session)
        except Exception as exc:
            logger.warning(f"Pre-warm skipped for Y{year} {identifier} {session}: {exc}")
            return

        # Each accessor is individually guarded: a missing/unpublished stream
        # (e.g. no telemetry for a session) must not abort warming the rest.
        accessors = [
            ("driver_list", store.driver_list),
            ("session_info", store.session_info),
            ("timing_data", store.timing_data),
            ("timing_app_data", store.timing_app_data),
            ("track_status", store.track_status),
            ("weather_data", store.weather_data),
            ("race_control", store.race_control),
            ("car_data", store.car_data),
            ("position_data", store.position_data),
            # Derived bundle (lap_times triggers the full bundle build + persist).
            ("lap_times", store.lap_times),
        ]
        warmed = 0
        for name, fn in accessors:
            try:
                fn()
                warmed += 1
            except Exception as exc:
                logger.debug(f"Pre-warm stream {name} skipped for Y{year} {identifier} {session}: {exc}")
        logger.info(f"🔥 Pre-warmed {warmed}/{len(accessors)} raw streams for Y{year} {identifier} {session}")

    async def process_session(self, year: int, gp: int, session: str, gp_name: str = None) -> Dict[str, Any]:
        """
        Process all data for a completed session

        Args:
            year: Year
            gp: Grand Prix round number (used for V1/FastF1)
            session: Session type
            gp_name: Grand Prix name string (used for V2/F1StaticClient to avoid
                     round-number mismatch caused by pre-season testing entries)

        Returns:
            Dictionary with processing results
        """
        session_id = f"{year}_{gp}_{session}"

        # Skip if already processed
        if session_id in self.processed_sessions:
            logger.debug(f"Session {session_id} already processed, skipping")
            return {"status": "skipped", "session": session_id, "reason": "already_processed"}

        logger.info(f"🔄 Starting automatic processing for Y{year} GP{gp} ({gp_name or gp}) {session}")

        # Pre-warm all raw V2 streams into the durable cache before generating
        # plots, so on-demand per-driver/per-pair requests hit a warm cache.
        if settings.enable_v2_stream_prewarm:
            try:
                await asyncio.to_thread(
                    self.prewarm_session_streams, year, gp, session, gp_name
                )
            except Exception as exc:
                logger.warning(f"Pre-warm step failed (continuing to plots): {exc}")

        try:
            # Generate all data for this session
            # V1 uses round number, V2 uses gp_name to avoid pre-season testing offset
            results = await asyncio.to_thread(
                self.generator.generate_all_session_data,
                year, gp, session,
                gp_name=gp_name  # passed as keyword arg
            )
            
            # Mark as processed
            self.processed_sessions.add(session_id)

            # Invalidate Redis response cache for this session so subsequent
            # API requests get the fresh data rather than stale "no data yet".
            try:
                from src.core.cache.redis_cache import get_sync_cache
                cache = get_sync_cache()
                if cache.enabled:
                    patterns = [
                        f"t1api:v2:plot:{year}:*:{session}:*",
                        f"t1api:v2:plot:{year}:*:{gp}:*",
                        "t1api:v2:latest_session",
                    ]
                    for p in patterns:
                        cache.delete_pattern(p) if "*" in p else cache.delete(p)
            except Exception as exc:
                logger.debug(f"Redis invalidation skipped after ingest: {exc}")

            logger.info(f"✅ Successfully processed Y{year} GP{gp} {session}")
            logger.info(f"   Generated {results.get('success', 0)} plot datasets ({results.get('failed', 0)} failed)")

            return {
                "status": "success",
                "session": session_id,
                "results": results
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to process Y{year} GP{gp} {session}: {e}", exc_info=True)
            return {
                "status": "error",
                "session": session_id,
                "error": str(e)
            }
    
    async def check_and_process_sessions(self):
        """
        Check for completed sessions and process them
        """
        logger.info("🔍 Checking for completed sessions to process...")
        
        try:
            # Get sessions that finished recently
            upcoming = self.get_upcoming_sessions()
            
            if not upcoming:
                logger.debug("No recently completed sessions found")
                return
            
            logger.info(f"Found {len(upcoming)} recently completed sessions to check")
            
            for year, gp, session, session_date in upcoming:
                session_id = f"{year}_{gp}_{session}"
                
                # Skip if already processed
                if session_id in self.processed_sessions:
                    continue
                
                # Check if session data is available
                if self.check_session_completed(year, gp, session):
                    logger.info(f"📊 New completed session detected: Y{year} GP{gp} {session}")
                    
                    # Process the session
                    result = await self.process_session(year, gp, session)
                    
                    # Small delay between sessions
                    await asyncio.sleep(5)
                else:
                    logger.debug(f"Session Y{year} GP{gp} {session} not yet ready for processing")
        
        except Exception as e:
            logger.error(f"Error in check_and_process_sessions: {e}", exc_info=True)
    
    async def run(self):
        """
        Main background processor loop
        Continuously checks for new sessions and processes them
        """
        self.running = True
        logger.info(f"🚀 Background processor started (check interval: {self.check_interval}s)")
        
        while self.running:
            try:
                await self.check_and_process_sessions()
                
                # Wait before next check
                logger.debug(f"Waiting {self.check_interval}s before next check...")
                await asyncio.sleep(self.check_interval)
                
            except Exception as e:
                logger.error(f"Error in background processor loop: {e}", exc_info=True)
                # Wait a bit before retrying
                await asyncio.sleep(60)
    
    def stop(self):
        """Stop the background processor"""
        logger.info("⏹️  Stopping background processor...")
        self.running = False
    
    async def force_process_latest(self):
        """
        Force process the latest completed session (useful for testing)
        """
        logger.info("🔄 Force processing latest session...")
        
        try:
            from src.services.orchestrator_helpers import get_latest_finished_session
            
            latest = get_latest_finished_session()
            if latest:
                result = await self.process_session(
                    latest['year'],
                    latest['round'],
                    latest['session_name'],
                    gp_name=latest.get('grandPrix')  # name-based V2 lookup
                )
                return result
            else:
                logger.warning("No latest session found")
                return {"status": "error", "error": "No latest session found"}
                
        except Exception as e:
            logger.error(f"Error force processing latest session: {e}", exc_info=True)
            return {"status": "error", "error": str(e)}


# Global instance
_processor: Optional[BackgroundProcessor] = None


def get_processor() -> BackgroundProcessor:
    """Get or create the global processor instance"""
    global _processor
    if _processor is None:
        from src.core.config import settings
        _processor = BackgroundProcessor(
            check_interval=settings.processor_check_interval
        )
    return _processor


async def start_background_processor():
    """Start the background processor as a background task"""
    processor = get_processor()
    await processor.run()


async def stop_background_processor():
    """Stop the background processor"""
    if _processor:
        _processor.stop()
