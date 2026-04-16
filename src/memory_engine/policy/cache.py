"""Prompt response cache.

Cache key is (site, prompt_hash, input_hash, persona_id). The persona_id
in the key prevents cross-persona cache poisoning (pitfall 6 in CLAUDE.md §13).

Phase 2 uses a simple in-memory LRU. Phase 6 may add a persistent layer
(SQLite or Redis) for cross-process sharing.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)

CacheKey = tuple[str, str, str, int]  # (site, prompt_hash, input_hash, persona_id)


class PromptCache:
    """LRU cache for parsed LLM responses.

    Thread-safe only if accessed from a single asyncio event loop (which is
    our execution model). Not safe for multi-threaded access.
    """

    def __init__(self, max_size: int = 256) -> None:
        self._max_size = max_size
        self._store: OrderedDict[CacheKey, dict[str, Any]] = OrderedDict()
        self._hits = 0
        self._misses = 0

    def get(self, key: CacheKey) -> dict[str, Any] | None:
        """Look up a cached response. Returns None on miss."""
        if key in self._store:
            self._store.move_to_end(key)
            self._hits += 1
            return self._store[key]
        self._misses += 1
        return None

    def put(self, key: CacheKey, value: dict[str, Any]) -> None:
        """Store a response. Evicts LRU entry if at capacity."""
        if key in self._store:
            self._store.move_to_end(key)
            self._store[key] = value
            return

        if len(self._store) >= self._max_size:
            evicted_key, _ = self._store.popitem(last=False)
            logger.debug("Cache evicted: site=%s persona=%d", evicted_key[0], evicted_key[3])

        self._store[key] = value

    def invalidate(self, site: str | None = None, persona_id: int | None = None) -> int:
        """Invalidate cache entries matching the given filters.

        Returns the number of entries removed.
        """
        keys_to_remove = []
        for key in self._store:
            if site is not None and key[0] != site:
                continue
            if persona_id is not None and key[3] != persona_id:
                continue
            keys_to_remove.append(key)

        for key in keys_to_remove:
            del self._store[key]

        return len(keys_to_remove)

    def clear(self) -> None:
        """Clear the entire cache."""
        self._store.clear()

    @property
    def stats(self) -> dict[str, int]:
        """Cache hit/miss statistics."""
        return {
            "hits": self._hits,
            "misses": self._misses,
            "size": len(self._store),
            "max_size": self._max_size,
        }
