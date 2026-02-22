import sys
from src.utils.database.mongo import MongoDBManager
manager = MongoDBManager(year=2025, version='v2')
col = manager._get_collection(2025)

# We need to remove the throttle_comparison data from the sessions array 
# for gp_id '2025_CHN' (Chinese GP) and session_type 'Q' (Quali)
gp_doc = col.find_one({"gp_id": "2025_CHN"})
if gp_doc:
    sessions = gp_doc.get("sessions", [])
    for s in sessions:
        if s.get("session_type") == "Q":
            new_data = [d for d in s.get("data", []) if d.get("data_type") != "throttle_comparison_drivers"]
            s["data"] = new_data
    col.update_one({"_id": gp_doc["_id"]}, {"$set": {"sessions": sessions}})
    print("Cleared Chinese GP 'Q' throttle_comparison cache!")
else:
    print("GP not found, cache clear skipped.")
manager.close()
