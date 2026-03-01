"""Debug script to inspect TimingData.jsonStream format"""

import requests

url = "https://livetiming.formula1.com/static/2023/2023-09-03_Italian_Grand_Prix/2023-09-03_Race/TimingData.jsonStream"

print("Fetching TimingData.jsonStream...")
response = requests.get(url)
content = response.content.decode('utf-8-sig')

print(f"\nTotal length: {len(content)} characters")
print(f"First 500 characters:\n")
print(repr(content[:500]))
print("\n" + "="*80)

# Check line endings
if '\r\n' in content[:500]:
    print("Line ending: CRLF (\\r\\n)")
elif '\n' in content[:500]:
    print("Line ending: LF (\\n)")
else:
    print("No line breaks detected in first 500 chars")

# Split and show first few lines
lines = content.split('\n')
print(f"\nTotal lines: {len(lines)}")
print(f"\nFirst 3 lines:")
for i, line in enumerate(lines[:3]):
    print(f"\nLine {i} (length {len(line)}):")
    print(repr(line[:200]))
