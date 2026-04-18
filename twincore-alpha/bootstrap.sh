#!/usr/bin/env bash
# TwinCore Alpha — bootstrap
# Run after: docker compose up -d memory-engine
# Generates: vault key, owner keypair, MCP keypair
# Creates: persona row, identity document
# Writes: .env with generated values

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$ROOT/.env"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "❌ .env not found. Copy .env.example → .env and fill in GEMINI_API_KEY first."
    exit 1
fi

# Load current .env
set -a; source "$ENV_FILE"; set +a

command -v python3 >/dev/null || { echo "❌ python3 required"; exit 1; }
command -v curl >/dev/null || { echo "❌ curl required"; exit 1; }

python3 -c "import nacl" 2>/dev/null || {
    echo "Installing PyNaCl..."
    pip3 install --user pynacl pyyaml
}

# Detect sed in-place flag (macOS vs GNU)
if sed --version >/dev/null 2>&1; then
    SED_INPLACE=(-i)
else
    SED_INPLACE=(-i '')
fi

# --------- 1. Generate vault key if missing ---------
if [[ "${MEMORY_ENGINE_VAULT_KEY:-}" == "GENERATED_AT_BOOTSTRAP" ]] || [[ -z "${MEMORY_ENGINE_VAULT_KEY:-}" ]]; then
    VAULT=$(python3 -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())")
    sed "${SED_INPLACE[@]}" "s|MEMORY_ENGINE_VAULT_KEY=.*|MEMORY_ENGINE_VAULT_KEY=$VAULT|" "$ENV_FILE"
    echo "✅ Generated MEMORY_ENGINE_VAULT_KEY"
fi

# --------- 2. Generate owner keypair if missing ---------
if [[ "${PERSONA_OWNER_PRIVATE_KEY:-}" == "GENERATED_AT_BOOTSTRAP" ]] || [[ -z "${PERSONA_OWNER_PRIVATE_KEY:-}" ]]; then
    read OWNER_PRIV OWNER_PUB <<<"$(python3 -c "
from nacl.signing import SigningKey
import base64
sk = SigningKey.generate()
print(base64.b64encode(bytes(sk)).decode(), base64.b64encode(bytes(sk.verify_key)).decode())
")"
    sed "${SED_INPLACE[@]}" "s|PERSONA_OWNER_PRIVATE_KEY=.*|PERSONA_OWNER_PRIVATE_KEY=$OWNER_PRIV|" "$ENV_FILE"
    sed "${SED_INPLACE[@]}" "s|PERSONA_OWNER_PUBLIC_KEY=.*|PERSONA_OWNER_PUBLIC_KEY=$OWNER_PUB|" "$ENV_FILE"
    echo "✅ Generated owner keypair"
fi

# --------- 3. Generate MCP signing key if missing ---------
if [[ "${MEMORY_ENGINE_MCP_PRIVATE_KEY:-}" == "GENERATED_AT_BOOTSTRAP" ]] || [[ -z "${MEMORY_ENGINE_MCP_PRIVATE_KEY:-}" ]]; then
    read MCP_PRIV MCP_PUB <<<"$(python3 -c "
from nacl.signing import SigningKey
import base64
sk = SigningKey.generate()
print(base64.b64encode(bytes(sk)).decode(), base64.b64encode(bytes(sk.verify_key)).decode())
")"
    sed "${SED_INPLACE[@]}" "s|MEMORY_ENGINE_MCP_PRIVATE_KEY=.*|MEMORY_ENGINE_MCP_PRIVATE_KEY=$MCP_PRIV|" "$ENV_FILE"
    echo "✅ Generated MCP signing keypair"
    echo "   MCP public key (for registration): $MCP_PUB"
    export MCP_PUB
fi

# Reload env
set -a; source "$ENV_FILE"; set +a

# --------- 4. Wait for memory-engine to be reachable ---------
echo "Waiting for memory_engine at http://localhost:4000 ..."
for i in $(seq 1 30); do
    if curl -sf "http://localhost:4000/health" >/dev/null 2>&1; then
        echo "✅ memory_engine is reachable"
        break
    fi
    if [[ $i -eq 30 ]]; then
        echo "❌ memory_engine did not come up. Run 'docker compose up -d memory-engine' first."
        exit 1
    fi
    sleep 2
done

# --------- 5. Create persona ---------
echo "Creating persona '$PERSONA_SLUG'..."
PERSONA_RESP=$(curl -sf -X POST "http://localhost:4000/v1/personas" \
    -H "Content-Type: application/json; charset=utf-8" \
    -d "{\"slug\":\"$PERSONA_SLUG\",\"owner_public_key\":\"${PERSONA_OWNER_PUBLIC_KEY}\"}" || echo "{}")
echo "Persona response: $PERSONA_RESP"

# --------- 6. Register MCP ---------
echo "Registering WhatsApp MCP..."
MCP_RESP=$(curl -sf -X POST "http://localhost:4000/v1/mcp/register" \
    -H "Content-Type: application/json; charset=utf-8" \
    -d "{\"persona_slug\":\"$PERSONA_SLUG\",\"kind\":\"whatsapp\",\"name\":\"primary\",\"public_key\":\"${MCP_PUB:-}\"}" || echo "{}")
echo "MCP response: $MCP_RESP"

# --------- 7. Sign identity document ---------
# NOTE: twin-agent reads personas/${PERSONA_SLUG}.yaml directly from disk for
# role/values/tone/non_negotiables, so signing the file is still required.
# We do NOT POST to /v1/identity/load — the twincore-alpha YAML schema
# (persona_slug, schema_version, issued_at, structured non_negotiables) does
# not match memory_engine's Phase 4 parse_identity_yaml (persona, version,
# signed_at, string non_negotiables). Schema harmonization is on the Phase 7
# backlog. See docs/blueprint/DRIFT.md entry
# `identity-schema-mismatch-twincore-vs-phase4`.
# Alpha consequence: memory_engine.personas.identity_doc stays NULL. Phase 4
# drift flags + outbound identity checks are unused until schema is unified.
# Twin-agent's persona tone, values, and non-negotiables still work because
# it reads the YAML directly.
echo "Signing identity document (alpha: not loaded into memory_engine — see DRIFT)..."
python3 <<PYEOF
import base64, json, yaml
from pathlib import Path
from nacl.signing import SigningKey

path = Path("personas/${PERSONA_SLUG}.yaml")
doc = yaml.safe_load(path.read_text(encoding="utf-8"))
doc["signature"] = ""
canonical = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
sk = SigningKey(base64.b64decode("${PERSONA_OWNER_PRIVATE_KEY}"))
sig = base64.b64encode(sk.sign(canonical).signature).decode("ascii")
doc["signature"] = sig
path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
print(f"✅ Signed identity written to {path}")
PYEOF

echo ""
echo "✅ Bootstrap complete."
echo ""
echo "Next steps:"
echo "  1. docker compose up -d whatsapp-bridge"
echo "  2. docker compose logs -f whatsapp-bridge   # scan QR code with phone"
echo "  3. docker compose up -d twin-agent control-plane"
echo "  4. Open http://localhost:4500 in browser"
