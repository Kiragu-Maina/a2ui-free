"""Probe every chat-capable NVIDIA NIM model with the current key, bucket by HTTP status."""

import json
import os
import re
import sys
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

env = {}
for line in Path(".env.test").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()

KEY = env["NVIDIA_NIM_API_KEY"]

# Load catalog
catalog = json.loads(Path("nvidia_models.json").read_text())
all_ids = sorted({m["id"] for m in catalog["data"]})

# Drop obvious non-chat (vision-only, embedding, safety, OCR, translation, video, reward)
NON_CHAT = re.compile(
    r"(embed|embedqa|nemoretriever|nemoguard|guard|safety|reward|nvclip|"
    r"riva-translate|deplot|kosmos|fuyu|vila|neva|cosmos-reason|"
    r"vision|multimodal|video-detector|gliner|parse|llama-guard|"
    r"arctic-embed|bge-m3|recurrentgemma)",
    re.IGNORECASE,
)
candidates = [i for i in all_ids if not NON_CHAT.search(i)]
print(f"Probing {len(candidates)} chat-capable models...\n", file=sys.stderr)

URL = "https://integrate.api.nvidia.com/v1/chat/completions"
HEADERS = {
    "Authorization": f"Bearer {KEY}",
    "Content-Type": "application/json",
}
PAYLOAD = lambda m: json.dumps({
    "model": m,
    "messages": [{"role": "user", "content": "Reply: ok"}],
    "max_tokens": 5,
    "stream": False,
}).encode()


def probe(model):
    req = urllib.request.Request(URL, data=PAYLOAD(model), headers=HEADERS, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read().decode("utf-8", errors="replace")
            try:
                j = json.loads(body)
                content = j.get("choices", [{}])[0].get("message", {}).get("content", "")
                return (model, r.status, (content or "").strip()[:40])
            except Exception:
                return (model, r.status, body[:60])
    except urllib.error.HTTPError as e:
        try:
            j = json.loads(e.read().decode("utf-8", errors="replace"))
            detail = j.get("detail") or j.get("message") or j.get("title") or str(j)[:80]
        except Exception:
            detail = "(no body)"
        return (model, e.code, str(detail)[:120])
    except Exception as e:
        return (model, 0, str(e)[:120])


results = []
with ThreadPoolExecutor(max_workers=8) as pool:
    futs = {pool.submit(probe, m): m for m in candidates}
    for f in as_completed(futs):
        results.append(f.result())

buckets = {}
for m, code, msg in results:
    buckets.setdefault(code, []).append((m, msg))

Path("probe_results.json").write_text(
    json.dumps({str(k): [{"id": m, "msg": msg} for m, msg in v] for k, v in buckets.items()}, indent=2)
)


def _safe(s):
    return s.encode("ascii", "replace").decode("ascii")


for code in sorted(buckets, key=lambda c: (c != 200, c)):
    label = "LIVE / FREE" if code == 200 else f"HTTP {code}"
    print(f"\n=== {label} ({len(buckets[code])}) ===")
    for m, msg in sorted(buckets[code]):
        print(f"  {m:<55} {_safe(msg)}")
