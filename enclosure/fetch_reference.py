"""Download the reference board model (not redistributed; CC BY-NC-SA).

"ESP32-C6-LCD-1.47 Reference CAD Model" by x_giedrius_x,
https://www.printables.com/model/1633740-esp32-c6-lcd-147-reference-cad-model
"""
import json
import sys
import urllib.request
from pathlib import Path

OUT = Path(__file__).parent / "ref" / "esp32_c6_lcd_147.stl"
API = "https://api.printables.com/graphql/"
QUERY = ('mutation { getDownloadLink(id: 6873072, printId: 1633740, fileType: stl, source: model_detail)'
         ' { ok output { link } } }')


def main() -> int:
    if OUT.exists():
        print(OUT)
        return 0
    req = urllib.request.Request(API, data=json.dumps({"query": QUERY}).encode(),
                                 headers={"content-type": "application/json", "user-agent": "Mozilla/5.0"})
    link = json.load(urllib.request.urlopen(req, timeout=20))["data"]["getDownloadLink"]["output"]["link"]
    OUT.parent.mkdir(exist_ok=True)
    with urllib.request.urlopen(urllib.request.Request(link, headers={"user-agent": "Mozilla/5.0"}), timeout=60) as r:
        OUT.write_bytes(r.read())
    print(OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
