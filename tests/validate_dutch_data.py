"""
Read and validate the Dutch GP data files
"""
import json

# Read the JSON file
with open("outputs/plots/2025/DutchGrandPrix/Qualifying/Top speed comparison 2025 Dutch Grand Prix Qualifying.json", 'r') as f:
    data = json.load(f)

print("="*80)
print("DATA FROM JSON FILE:")
print("="*80)
for item in data:
    print(f"{item['Team']:20s}: {item['Top Speed (km/h)']:.1f} km/h")

print("\n" + "="*80)
print("EXPECTED FROM USER'S PLOT IMAGE:")
print("="*80)
print("Williams            : 334 km/h")
print("Red Bull Racing     : 333 km/h")
print("Ferrari             : 332 km/h")
print("Aston Martin        : 332 km/h")
print("McLaren             : 330 km/h")
print("Haas F1 Team        : 330 km/h")
print("Kick Sauber         : 330 km/h")
print("Alpine              : 329 km/h")
print("Racing Bulls        : 327 km/h")
print("Mercedes            : 326 km/h")

print("\n" + "="*80)
print("ANALYSIS:")
print("="*80)
print("JSON shows 342 km/h (too high)")
print("User's plot shows 334 km/h (correct)")
print("→ Mismatch! JSON and plot don't align")
print("\nPossibility: User's plot image is from an older/different run")
print("              OR there's a different data source we should use")
