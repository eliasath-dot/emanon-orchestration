from __future__ import annotations

import hashlib
import hmac
import json
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

ACTION_PATH_INTEGRATION = "PATH_INTEGRATION"
ACTION_LEVY_FLIGHT = "LEVY_FLIGHT"


@dataclass(frozen=True)
class AuditLogEntry:
    timestamp_ns: int
    agent_a: str
    agent_b: str
    metadata: Dict[str, Any]
    prev_signature: str
    signature: str


class InteractionPolicy:
    def __init__(self) -> None:
        self.relation_value_map = {
            "Causally Drives": 3.0, "Resolves": 2.5, "Enhances": 1.5,
            "Mentions": 0.5, "Conflicts": -1.0,
        }

    def get_interaction_weight(self, interaction_type: str) -> float:
        if interaction_type not in self.relation_value_map:
            raise ValueError(f"Unknown interaction type: {interaction_type}")
        return self.relation_value_map[interaction_type]


class AuditLogger:
    GENESIS_SIGNATURE = "0" * 64

    def __init__(self, secret_key: str) -> None:
        if not secret_key or secret_key == "default_unsafe_key":
            raise ValueError("A secure secret key is strictly required for the Audit Logger.")
        self.secret_key = secret_key.encode("utf-8")
        self.chain: List[AuditLogEntry] = []
        self.last_signature = self.GENESIS_SIGNATURE

    def _compute_hmac(self, payload: str) -> str:
        return hmac.new(self.secret_key, payload.encode("utf-8"), hashlib.sha256).hexdigest()

    def _build_payload_obj(self, timestamp_ns, agent_a, agent_b, metadata, prev_signature):
        return {"timestamp_ns": timestamp_ns, "agent_a": agent_a, "agent_b": agent_b,
                "metadata": dict(metadata), "prev_signature": prev_signature}

    def _serialize_payload(self, payload_obj):
        try:
            return json.dumps(payload_obj, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise TypeError(f"Payload must be JSON-serializable and JSON-safe: {exc}") from exc

    def record_handshake(self, agent_a, agent_b, metadata):
        if not isinstance(agent_a, str) or not agent_a:
            raise ValueError("agent_a must be a non-empty string.")
        if not isinstance(agent_b, str) or not agent_b:
            raise ValueError("agent_b must be a non-empty string.")
        if not isinstance(metadata, dict):
            raise TypeError("Metadata must be a dictionary.")
        timestamp_ns = time.time_ns()
        payload_obj = self._build_payload_obj(timestamp_ns, agent_a, agent_b, metadata, self.last_signature)
        signature = self._compute_hmac(self._serialize_payload(payload_obj))
        entry = AuditLogEntry(timestamp_ns, agent_a, agent_b, dict(metadata), self.last_signature, signature)
        self.chain.append(entry)
        self.last_signature = signature
        return signature

    def verify_chain(self, head: Optional[Mapping[str, Any]] = None) -> bool:
        """Verify every link in the chain.

        Without `head`, this detects modification, reordering and forgery of
        entries, but NOT truncation of the tail: a chain cut short is still
        internally consistent. Pass a `head` previously obtained from `head()`
        and stored separately (another file, another host) to also check that
        the chain still has the sealed length and ends at the sealed signature.
        """
        prev = self.GENESIS_SIGNATURE
        try:
            for entry in self.chain:
                if entry.prev_signature != prev:
                    return False
                payload_obj = self._build_payload_obj(entry.timestamp_ns, entry.agent_a, entry.agent_b,
                                                      entry.metadata, entry.prev_signature)
                expected = self._compute_hmac(self._serialize_payload(payload_obj))
                if not hmac.compare_digest(expected, entry.signature):
                    return False
                prev = entry.signature
        except (AttributeError, KeyError, TypeError, ValueError):
            return False
        if head is not None:
            return self._verify_head(head, length=len(self.chain), last_signature=prev)
        return True

    # --- Sealed head: makes truncation detectable --------------------------

    def head(self) -> Dict[str, Any]:
        """Return a signed commitment to the chain's current length and tail.

        Store it somewhere the chain itself cannot be edited from. Verifying
        with it later proves the chain has not been cut short or extended.
        """
        length = len(self.chain)
        return {
            "length": length,
            "last_signature": self.last_signature,
            "seal": self._compute_hmac(self._head_payload(length, self.last_signature)),
        }

    @staticmethod
    def _head_payload(length: int, last_signature: str) -> str:
        return json.dumps({"length": length, "last_signature": last_signature},
                          sort_keys=True, separators=(",", ":"))

    def _verify_head(self, head: Mapping[str, Any], length: int, last_signature: str) -> bool:
        try:
            if not isinstance(head["length"], int) or isinstance(head["length"], bool):
                return False
            expected_seal = self._compute_hmac(self._head_payload(head["length"], head["last_signature"]))
            if not hmac.compare_digest(expected_seal, str(head["seal"])):
                return False  # the head itself was tampered with
            return head["length"] == length and hmac.compare_digest(head["last_signature"], last_signature)
        except (KeyError, TypeError):
            return False

    # --- Persistence: append-only JSONL -----------------------------------

    def export_chain(self):
        return [asdict(e) for e in self.chain]

    def save_jsonl(self, path) -> None:
        """Write the chain as one JSON object per line (overwrites the file)."""
        with Path(path).open("w", encoding="utf-8") as fh:
            for entry in self.chain:
                fh.write(json.dumps(asdict(entry), sort_keys=True, separators=(",", ":"),
                                    allow_nan=False) + "\n")

    def append_jsonl(self, path, entry: AuditLogEntry) -> None:
        """Append one entry to a JSONL file. Pair with record_handshake's return."""
        with Path(path).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(entry), sort_keys=True, separators=(",", ":"),
                                allow_nan=False) + "\n")

    @classmethod
    def load_jsonl(cls, secret_key: str, path, head: Optional[Mapping[str, Any]] = None) -> "AuditLogger":
        """Rebuild a logger from a JSONL file and verify it.

        Raises ValueError if the file fails verification, so a corrupted or
        truncated log is never silently resumed. Pass `head` to also detect
        truncation (see verify_chain).
        """
        logger = cls(secret_key)
        with Path(path).open("r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                    entry = AuditLogEntry(**raw)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{path}: malformed entry on line {line_no}: {exc}") from exc
                logger.chain.append(entry)
        if logger.chain:
            logger.last_signature = logger.chain[-1].signature
        if not logger.verify_chain(head=head):
            raise ValueError(f"{path}: audit chain failed verification"
                             + (" against the supplied head" if head is not None else ""))
        return logger


class ThresholdOptimizer:
    # Defaults are operator choices, not measured constants.
    #   waste_weight=1.2   : one unit of compute must buy more than 1.2 units of value to break even.
    #   waste_exponent=1.05: slightly superlinear, so doubling spend must more than double value.
    def __init__(self, waste_weight=1.2, waste_exponent=1.05):
        if not math.isfinite(waste_weight) or waste_weight < 0:
            raise ValueError("waste_weight must be finite and non-negative.")
        if not math.isfinite(waste_exponent) or waste_exponent <= 0:
            raise ValueError("waste_exponent must be finite and positive.")
        self.waste_weight = waste_weight
        self.waste_exponent = waste_exponent

    def step(self, clarity, quality, v_recycle, raw_compute_cost):
        for name, value in {"clarity": clarity, "quality": quality,
                            "v_recycle": v_recycle, "raw_compute_cost": raw_compute_cost}.items():
            if not math.isfinite(value):
                raise ValueError(f"Input '{name}' must be finite. Received: {value}")
        if raw_compute_cost < 0:
            raise ValueError("Compute cost cannot be negative.")
        c_waste = self.waste_weight * (raw_compute_cost ** self.waste_exponent)
        value_term = clarity * quality
        reward = value_term + v_recycle - c_waste
        if reward > 1e-9:
            status = "System Aligned: Sustainable Innovation"
        elif abs(reward) < 1e-9:
            status = "System Balanced: Zero-Cost Coordination"
        else:
            status = "System Drifting: Compute Waste Exceeds Value (Circuit Breaker Triggered)"
        return {"net_reward": reward, "value_generated": value_term, "recycled_value": v_recycle,
                "compute_waste": c_waste, "status": status}


class EmanonMetaController:
    # drift_threshold=0.75 (cosine): roughly a 41 degree cone around the root objective;
    # a starting point to tune per embedding model, not a calibrated value.
    def __init__(self, root_vector, drift_threshold=0.75):
        if not -1.0 <= drift_threshold <= 1.0:
            raise ValueError("drift_threshold must be between -1.0 and 1.0")
        self.root_embedding = np.array(root_vector, dtype=float)
        self.drift_threshold = drift_threshold
        norm = np.linalg.norm(self.root_embedding)
        if norm == 0:
            raise ValueError("root_vector must be non-zero to establish a meaningful trajectory reference.")
        self.root_embedding = self.root_embedding / norm

    def similarity_to_root(self, vec2):
        if self.root_embedding.shape != vec2.shape:
            raise ValueError("Embedding dimension mismatch.")
        norm_b = np.linalg.norm(vec2)
        if norm_b == 0:
            return 0.0
        return float(np.dot(self.root_embedding, vec2) / norm_b)

    def evaluate_trajectory(self, agent_embedding):
        cos_sim = self.similarity_to_root(np.array(agent_embedding, dtype=float))
        if cos_sim >= self.drift_threshold:
            return ACTION_PATH_INTEGRATION, cos_sim
        return ACTION_LEVY_FLIGHT, cos_sim
