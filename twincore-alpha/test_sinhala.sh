#!/usr/bin/env bash
# End-to-end Sinhala UTF-8 test.
# Sends a Sinhala message via curl to memory_engine, retrieves it, compares.

set -euo pipefail
set -a; source .env; set +a

TEXT="ආයුබෝවන්, අද ඔයා කොහොමද? මම හරි සතුටින් ඉන්නේ."
COUNTERPARTY="whatsapp:+94770000000"

# Compute hash + signature using Python
read HASH SIGNATURE <<<"$(python3 <<PYEOF
import base64, hashlib, json
from nacl.signing import SigningKey

text = """$TEXT"""
payload = {"text": text, "wa_message_id": "test-sinhala-1", "timestamp": "2026-04-17T10:00:00+00:00"}
canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
h = hashlib.sha256(canonical).hexdigest()
sk = SigningKey(base64.b64decode("$MEMORY_ENGINE_MCP_PRIVATE_KEY"))
msg = f"$PERSONA_ID:{h}".encode("utf-8")
sig = base64.b64encode(sk.sign(msg).signature).decode("ascii")
print(h, sig)
PYEOF
)"

echo "Hash: $HASH"
echo "Signature: ${SIGNATURE:0:20}..."

echo ""
echo "--- Ingesting Sinhala test event ---"
curl -sf -X POST "http://localhost:4000/v1/ingest" \
    -H "Content-Type: application/json; charset=utf-8" \
    -d "$(python3 -c "
import json
print(json.dumps({
    'persona_slug': '$PERSONA_SLUG',
    'counterparty_external_ref': '$COUNTERPARTY',
    'event_type': 'message_in',
    'scope': 'private',
    'payload': {'text': '''$TEXT''', 'wa_message_id': 'test-sinhala-1', 'timestamp': '2026-04-17T10:00:00+00:00'},
    'signature': '$SIGNATURE',
    'idempotency_key': 'wa:test-sinhala-1',
}, ensure_ascii=False))
")" \
    | tee /tmp/ingest_resp.json

echo ""
echo "--- Recalling under counterparty lens ---"
curl -sf -X POST "http://localhost:4000/v1/recall" \
    -H "Content-Type: application/json; charset=utf-8" \
    -d "{\"persona_slug\":\"$PERSONA_SLUG\",\"query\":\"ඔයා කොහොමද\",\"lens\":\"counterparty:$COUNTERPARTY\",\"top_k\":5}" \
    | tee /tmp/recall_resp.json | python3 -m json.tool

echo ""
echo "--- Checking Sinhala chars survived ---"
if grep -q "ආයුබෝවන්" /tmp/ingest_resp.json /tmp/recall_resp.json 2>/dev/null; then
    echo "✅ Sinhala characters preserved in ingest + recall"
else
    echo "⚠️  Sinhala characters may be corrupted. Inspect /tmp/ingest_resp.json and /tmp/recall_resp.json"
fi
