from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from pymongo.errors import OperationFailure, PyMongoError
from urllib.parse import quote_plus
from datetime import datetime
from typing import Dict, List, Any, Optional
import json
import os
import threading
from dotenv import load_dotenv
import numpy as np

# Load environment variables
load_dotenv()


# ---------------------------------------------------------------------------
# Process-wide MongoClient singleton with proper pool wiring.
# Prior to this, every MongoDBManager() constructed its own client and
# pool config from src/core/config.py was read but never passed to PyMongo.
# ---------------------------------------------------------------------------
_MONGO_CLIENT: Optional[MongoClient] = None
_MONGO_CLIENT_LOCK = threading.Lock()


def _build_mongo_client() -> MongoClient:
    """Build a properly pooled MongoClient using settings (pool, timeout, appname)."""
    from src.core.config import settings  # local import to avoid cycles at import time

    username = os.getenv('MONGODB_USER', settings.mongodb_user)
    password = os.getenv('MONGODB_PASSWORD', settings.mongodb_password)
    host = os.getenv('MONGODB_HOST', settings.mongodb_host)
    port = os.getenv('MONGODB_PORT', str(settings.mongodb_port))

    if not password:
        raise ValueError("MONGODB_PASSWORD environment variable is required")

    encoded_password = quote_plus(password)
    uri = f"mongodb://{username}:{encoded_password}@{host}:{port}/?directConnection=true"
    return MongoClient(
        uri,
        server_api=ServerApi('1'),
        maxPoolSize=settings.mongodb_max_pool_size,
        minPoolSize=settings.mongodb_min_pool_size,
        serverSelectionTimeoutMS=settings.mongodb_timeout_ms,
        appname="t1api",
    )


def get_mongo_client() -> MongoClient:
    """Return the shared, lazily-initialized MongoClient."""
    global _MONGO_CLIENT
    if _MONGO_CLIENT is None:
        with _MONGO_CLIENT_LOCK:
            if _MONGO_CLIENT is None:
                _MONGO_CLIENT = _build_mongo_client()
    return _MONGO_CLIENT


class NumpyEncoder(json.JSONEncoder):
    """Custom JSON encoder for numpy types"""
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        return super(NumpyEncoder, self).default(obj)


def convert_numpy_types(obj):
    """
    Recursively convert numpy types to native Python types for MongoDB compatibility

    Args:
        obj: Object to convert (dict, list, or primitive)

    Returns:
        Object with numpy types converted to Python types
    """
    if isinstance(obj, dict):
        return {key: convert_numpy_types(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [convert_numpy_types(item) for item in obj]
    elif isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, np.bool_):
        return bool(obj)
    else:
        return obj


class MongoDBManager:
    """
    MongoDB Manager for F1 Telemetry Data Storage
    Handles connection and CRUD operations for race session data
    """

    def __init__(self, year: Optional[int] = None, version: str = 'v1'):
        """
        Initialize MongoDB Manager
        
        Args:
            year: Year for the collection
            version: Data source version ('v1' for FastF1, 'v2' for F1StaticClient)
                    This affects collection naming to prevent data conflicts due to
                    different round number indexing between sources.
        """
        database_name = os.getenv('MONGODB_DATABASE', 'T1API_DB')
        host = os.getenv('MONGODB_HOST', 'localhost')
        port = os.getenv('MONGODB_PORT', '27017')

        # Share the process-wide pooled client. Previously each manager
        # constructed its own MongoClient, defeating connection pooling.
        self.client = get_mongo_client()
        self.db = self.client[database_name]

        # Set collection based on year and version (dynamically switch collections)
        self.current_year = year
        self.version = version
        self.collection = self._get_collection(year, version)

        # Test connection
        try:
            self.client.admin.command('ping')
            collection_name = self.collection.name
            print(f"✓ Successfully connected to MongoDB at {host}:{port}")
            print(f"✓ Using collection: {collection_name} (version: {version})")
        except Exception as e:
            print(f"✗ MongoDB connection failed: {e}")
            raise

    def _get_collection(self, year: Optional[int] = None, version: Optional[str] = None):
        """
        Get the collection for a specific year and version

        Args:
            year: Year for the collection. If None, uses current year or defaults to 2025
            version: Data source version ('v1' or 'v2'). If None, uses instance version

        Returns:
            MongoDB collection object
        """
        if year is None:
            year = datetime.now().year
        
        if version is None:
            version = getattr(self, 'version', 'v1')
        
        # v1 uses standard naming, v2 adds version suffix for separation
        if version == 'v2':
            collection_name = f"{year}_processed_data_v2"
        else:
            collection_name = f"{year}_processed_data"
        
        return self.db[collection_name]

    def set_year(self, year: int):
        """
        Switch to a different year's collection

        Args:
            year: Year to switch to
        """
        self.current_year = year
        self.collection = self._get_collection(year, self.version)
        print(f"✓ Switched to collection: {self.collection.name}")

    def get_current_year(self) -> int:
        """
        Get the current year being used

        Returns:
            Current year
        """
        return self.current_year if self.current_year else datetime.now().year

    def create_gp_document(self, year: int, round_nr: int, gp_name: str, gp_id: str, session: Any) -> Dict:
        """
        Create a new Grand Prix document with session structure

        Args:
            year: Race year
            round_nr: Round number in the season
            gp_name: Grand Prix name (e.g., "AustralianGP")
            gp_id: Unique GP identifier (e.g., "2025_AUS")
            session: FastF1 session object

        Returns:
            The created document
        """
        # Ensure we're using the correct collection for this year
        collection = self._get_collection(year)

        # Create base document
        document = {
            "year": year,
            "round_nr": round_nr,
            "name": gp_name,
            "gp_id": gp_id,
            "sessions": []
        }

        return document

    def get_or_create_gp(self, year: int, round_nr: int, gp_name: str, gp_id: str) -> Dict:
        """
        Get existing GP document or create new one

        Args:
            year: Race year
            round_nr: Round number
            gp_name: Grand Prix name
            gp_id: Unique GP identifier

        Returns:
            The GP document
        """
        # Use the collection for the specific year
        collection = self._get_collection(year)

        # Try to find existing document
        existing_doc = collection.find_one({"gp_id": gp_id})

        if existing_doc:
            return existing_doc

        # Create new document if doesn't exist
        # Convert all values to native Python types for MongoDB compatibility
        new_doc = {
            "year": int(year),
            "round_nr": int(round_nr),
            "name": str(gp_name),
            "gp_id": str(gp_id),
            "sessions": []
        }

        # Convert numpy types if any
        new_doc = convert_numpy_types(new_doc)

        result = collection.insert_one(new_doc)
        new_doc['_id'] = result.inserted_id
        print(f"✓ Created new GP document: {gp_id} in collection {collection.name}")

        return new_doc

    def add_session_data(self, gp_id: str, session_type: str, data_type: str,
                        data: List[Dict], date: str = None, time: str = None, year: Optional[int] = None) -> bool:
        """
        Add or update data for a specific session and data type

        Args:
            gp_id: Grand Prix identifier (e.g., "2025_AUS")
            session_type: Session type (FP1, FP2, FP3, Q, S, R)
            data_type: Type of data (e.g., "top_speed", "throttle_comparison_drivers")
            data: List of data records
            date: Session date (YYYY-MM-DD)
            time: Session time (HH:MM:SSZ)
            year: Optional year to specify collection. If None, extracts from gp_id

        Returns:
            True if successful, False otherwise
        """
        try:
            # Extract year from gp_id if not provided (e.g., "2025_AUS" -> 2025)
            if year is None:
                year = int(gp_id.split('_')[0])

            # Use the collection for the specific year (with current version)
            collection = self._get_collection(year, self.version)

            # Convert numpy types in data
            data = convert_numpy_types(data)

            # Find the GP document
            gp_doc = collection.find_one({"gp_id": gp_id})

            if not gp_doc:
                print(f"✗ GP document not found: {gp_id}")
                return False

            # Find or create session
            sessions = gp_doc.get('sessions', [])
            session_index = None

            for idx, session in enumerate(sessions):
                if session.get('session_type') == session_type:
                    session_index = idx
                    break

            # Prepare data entry
            data_entry = {
                "data_type": data_type,
                "data": data
            }

            if session_index is not None:
                # Session exists, check if data_type exists
                session_data = sessions[session_index].get('data', [])
                data_type_index = None

                for idx, entry in enumerate(session_data):
                    if entry.get('data_type') == data_type:
                        data_type_index = idx
                        break

                if data_type_index is not None:
                    # Update existing data
                    collection.update_one(
                        {"gp_id": gp_id},
                        {"$set": {f"sessions.{session_index}.data.{data_type_index}": data_entry}}
                    )
                    print(f"✓ Updated {data_type} for {gp_id} - {session_type}")
                else:
                    # Add new data type to existing session
                    collection.update_one(
                        {"gp_id": gp_id},
                        {"$push": {f"sessions.{session_index}.data": data_entry}}
                    )
                    print(f"✓ Added {data_type} to {gp_id} - {session_type}")
            else:
                # Create new session with data
                new_session = {
                    "session_type": session_type,
                    "data": [data_entry]
                }

                if date:
                    new_session["date"] = date
                if time:
                    new_session["time"] = time

                collection.update_one(
                    {"gp_id": gp_id},
                    {"$push": {"sessions": new_session}}
                )
                print(f"✓ Created new session and added {data_type} for {gp_id} - {session_type}")

            return True

        except Exception as e:
            print(f"✗ Error adding session data: {e}")
            import traceback
            traceback.print_exc()
            return False

    def store_plot_data(self, year: int, round_nr: int, session_type: str,
                       data_type: str, json_file_path: str) -> bool:
        """
        Store plot data from a JSON file into MongoDB

        Args:
            year: Race year
            round_nr: Round number
            session_type: Session type (FP1, FP2, etc.)
            data_type: Type of plot data (top_speed, throttle_comparison_drivers, etc.)
            json_file_path: Path to the JSON file with plot data

        Returns:
            True if successful, False otherwise
        """
        try:
            # Read JSON data
            with open(json_file_path, 'r') as f:
                data = json.load(f)

            # Generate GP ID (you may need to adjust this based on your naming convention)
            # For now, using a simple format
            gp_id = f"{year}_R{round_nr:02d}"

            # Get or create GP document
            gp_doc = self.get_or_create_gp(year, round_nr, f"Round{round_nr}", gp_id)

            # Add session data
            success = self.add_session_data(gp_id, session_type, data_type, data, year=year)

            return success

        except Exception as e:
            print(f"✗ Error storing plot data: {e}")
            return False

    def get_session_data(self, gp_id: str, session_type: str, data_type: Optional[str] = None, year: Optional[int] = None) -> Optional[Dict]:
        """
        Retrieve data for a specific session

        Args:
            gp_id: Grand Prix identifier
            session_type: Session type (FP1, FP2, Q, etc.)
            data_type: Optional specific data type to retrieve
            year: Optional year to specify collection. If None, extracts from gp_id

        Returns:
            Session data or None if not found
        """
        try:
            # Extract year from gp_id if not provided
            if year is None:
                year = int(gp_id.split('_')[0])

            collection = self._get_collection(year)

            # Project only the matching session so we don't transfer or iterate
            # the whole GP document (other sessions can carry megabytes of data).
            gp_doc = collection.find_one(
                {"gp_id": gp_id, "sessions.session_type": session_type},
                {"sessions": {"$elemMatch": {"session_type": session_type}}},
            )

            if not gp_doc:
                return None

            sessions = gp_doc.get("sessions", [])
            if not sessions:
                return None
            session = sessions[0]

            if data_type:
                for data_entry in session.get('data', []):
                    if data_entry.get('data_type') == data_type:
                        return data_entry.get('data')
                return None
            return session

        except Exception as e:
            print(f"✗ Error retrieving session data: {e}")
            return None

    def delete_session_data(self, gp_id: str, session_type: str, data_type: str,
                            year: Optional[int] = None) -> bool:
        """Remove one stored ``data_type`` from a session.

        Backs the admin data browser: a payload generated from bad upstream data
        has to be removable, otherwise the inventory reports it as present
        forever and the backfill skips regenerating it. Pulls only the matching
        array entry, leaving the rest of the session document untouched.
        """
        try:
            if year is None:
                year = int(gp_id.split('_')[0])

            result = self._get_collection(year).update_one(
                {"gp_id": gp_id, "sessions.session_type": session_type},
                {"$pull": {"sessions.$.data": {"data_type": data_type}}},
            )
            return result.modified_count > 0
        except Exception as e:
            print(f"✗ Error deleting session data: {e}")
            return False

    def summarize_stored_data(self, year: int) -> List[Dict]:
        """Every stored ``data_type`` per GP/session, for the admin data browser.

        Returns keys and payload sizes only — never the payloads themselves,
        which run to megabytes per session.
        """
        rows: List[Dict] = []
        try:
            for gp in self.list_all_gps(year=year):
                for sess in gp.get("sessions", []) or []:
                    entries = sess.get("data", []) or []
                    rows.append({
                        "year": year,
                        "gp_id": gp.get("gp_id"),
                        "event_name": gp.get("name"),
                        "round_nr": gp.get("round_nr"),
                        "session_type": sess.get("session_type"),
                        "data_types": sorted(
                            str(e.get("data_type")) for e in entries
                            if isinstance(e, dict) and e.get("data_type")
                        ),
                        "count": len(entries),
                    })
        except Exception as e:
            print(f"✗ Error summarizing stored data: {e}")
        return sorted(rows, key=lambda r: (r.get("round_nr") or 0, r.get("session_type") or ""))

    def get_all_gp_data(self, gp_id: str, year: Optional[int] = None) -> Optional[Dict]:
        """
        Retrieve all data for a Grand Prix

        Args:
            gp_id: Grand Prix identifier
            year: Optional year to specify collection. If None, extracts from gp_id

        Returns:
            Complete GP document or None
        """
        try:
            # Extract year from gp_id if not provided
            if year is None:
                year = int(gp_id.split('_')[0])

            collection = self._get_collection(year)
            gp_doc = collection.find_one({"gp_id": gp_id}, {"_id": 0})
            return gp_doc
        except Exception as e:
            print(f"✗ Error retrieving GP data: {e}")
            return None

    def list_all_gps(self, year: Optional[int] = None) -> List[Dict]:
        """
        List all Grand Prix events

        Args:
            year: Optional filter by year. If None, uses current collection year

        Returns:
            List of GP documents
        """
        try:
            if year is None:
                year = self.get_current_year()

            collection = self._get_collection(year)
            query = {"year": year}
            gps = list(collection.find(query, {"_id": 0}).sort("round_nr", 1))
            return gps
        except Exception as e:
            print(f"✗ Error listing GPs: {e}")
            return []

    def list_all_years(self) -> List[int]:
        """
        List all available years (collections) in the database

        Returns:
            List of years with data
        """
        try:
            # Get all collection names
            collection_names = self.db.list_collection_names()

            # Extract years from collection names (format: YYYY_processed_data)
            years = []
            for name in collection_names:
                if name.endswith('_processed_data'):
                    try:
                        year = int(name.split('_')[0])
                        years.append(year)
                    except ValueError:
                        continue

            return sorted(years)
        except Exception as e:
            print(f"✗ Error listing years: {e}")
            return []

    def close(self):
        """Release the manager reference. The shared MongoClient stays open
        for the life of the process; closing the underlying pool would defeat
        the singleton and force every subsequent caller to re-handshake."""
        # Intentionally no-op on the shared client.
        pass


# ---------------------------------------------------------------------------
# Indexes — created idempotently at app startup.
# Until this landed there were zero indexes anywhere in the DB, so every
# read of plot/session data was a collection scan with linear array iteration.
# ---------------------------------------------------------------------------
def ensure_indexes(years: Optional[List[int]] = None) -> Dict[str, List[str]]:
    """Create the indexes the codebase actually queries on.

    Idempotent: re-running on an existing index is a no-op. Errors on
    individual indexes are logged and swallowed so a single bad collection
    cannot prevent app startup.

    Args:
        years: Optional list of season years for which to create the
            ``{year}_processed_data[_v2]`` indexes. Defaults to the current
            and previous calendar year (the only collections the API serves
            in the hot path).

    Returns:
        Mapping of ``collection -> [index_name, ...]`` created or already
        present, for diagnostics.
    """
    from src.core.logging import get_logger
    logger = get_logger(__name__)

    if years is None:
        current = datetime.now().year
        years = [current - 1, current, current + 1]

    client = get_mongo_client()
    db = client[os.getenv('MONGODB_DATABASE', 'T1API_DB')]
    created: Dict[str, List[str]] = {}

    def _safe_create(coll_name: str, keys, **opts):
        try:
            name = db[coll_name].create_index(keys, background=True, **opts)
            created.setdefault(coll_name, []).append(name)
        except (OperationFailure, PyMongoError) as exc:
            logger.warning("Index create skipped on %s %s: %s", coll_name, keys, exc)

    for y in years:
        for suffix in ("processed_data", "processed_data_v2"):
            coll = f"{y}_{suffix}"
            _safe_create(coll, [("gp_id", 1)])
            _safe_create(coll, [("year", 1), ("round_nr", 1)])
            _safe_create(coll, [("year", 1), ("gp_id", 1)])

    # Seasons + reference data
    _safe_create("seasons", [("year", 1)], unique=False)

    # Auth: users, api_keys, api_key_usage. Keyed lookups are on the hot
    # auth path so these MUST exist; the unique constraints also guard
    # against accidental duplicates.
    _safe_create("users", [("email", 1)], unique=True)
    _safe_create("api_keys", [("key_hash", 1)], unique=True)
    _safe_create("api_keys", [("owner_id", 1)])
    _safe_create("api_key_usage", [("key_hash", 1), ("bucket", 1)], unique=True)
    # TTL on api_key_usage so it self-prunes (90 days).
    from src.repositories.api_key_usage import USAGE_TTL_DAYS
    _safe_create(
        "api_key_usage",
        [("created_at", 1)],
        expireAfterSeconds=USAGE_TTL_DAYS * 86400,
    )

    # Admin background jobs: the history page sorts newest-first and the
    # overlap guard filters on status.
    from src.repositories.admin_jobs import COLLECTION as ADMIN_JOBS_COLLECTION
    _safe_create(ADMIN_JOBS_COLLECTION, [("created_at", -1)])
    _safe_create(ADMIN_JOBS_COLLECTION, [("status", 1), ("created_at", -1)])

    # Durable V2 caches. Both are addressed by _id, but the admin cache
    # inventory page scans them by session and by age.
    _safe_create("v2_session_cache", [("year", 1), ("session", 1)])
    _safe_create("v2_session_cache", [("created_at", -1)])
    _safe_create("v2_raw_cache.files", [("metadata.year", 1), ("metadata.session", 1)])

    return created


# Convenience function for quick testing
def test_connection():
    """Test MongoDB connection"""
    try:
        manager = MongoDBManager()
        print("MongoDB connection test successful!")
        manager.close()
        return True
    except Exception as e:
        print(f"MongoDB connection test failed: {e}")
        return False


if __name__ == "__main__":
    test_connection()
