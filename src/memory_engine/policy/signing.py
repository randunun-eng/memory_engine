"""Ed25519 signing for MCP sources.

Keypair generation is a one-time operator action, not done by the engine in
production. Test fixtures generate keys as needed.
"""

from __future__ import annotations

import base64

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

from memory_engine.exceptions import SignatureInvalid


def generate_keypair() -> tuple[bytes, bytes]:
    """Generate a new Ed25519 keypair. Returns (private_key_bytes, public_key_bytes).

    Used at MCP registration time. The private key is shown once to the operator
    and never stored by the engine.
    """
    signing_key = SigningKey.generate()
    return bytes(signing_key), bytes(signing_key.verify_key)


def sign(private_key: bytes, message: bytes) -> str:
    """Sign a message. Returns base64-encoded signature."""
    signing_key = SigningKey(private_key)
    signed = signing_key.sign(message)
    return base64.b64encode(signed.signature).decode("ascii")


def verify(public_key_b64: str, message: bytes, signature_b64: str) -> None:
    """Verify a signature. Raises SignatureInvalid on failure.

    Args:
        public_key_b64: Base64-encoded public key, as stored in mcp_sources.
        message: The signed bytes. Typically canonical form of (persona_id, content_hash).
        signature_b64: Base64-encoded signature.

    Raises:
        SignatureInvalid: If the signature does not verify.
    """
    try:
        public_key = base64.b64decode(public_key_b64)
        signature = base64.b64decode(signature_b64)
        verify_key = VerifyKey(public_key)
        verify_key.verify(message, signature)
    except BadSignatureError as e:
        raise SignatureInvalid("Signature verification failed") from e
    except ValueError as e:
        raise SignatureInvalid(f"Invalid key or signature encoding: {e}") from e


def canonical_signing_message(persona_id: int, content_hash: str) -> bytes:
    """Canonical bytes to sign for an event.

    The MCP signs (persona_id || content_hash). The engine verifies against
    the same canonical form. Any change here requires coordination with every
    MCP; treat as contract.
    """
    return f"{persona_id}:{content_hash}".encode()
