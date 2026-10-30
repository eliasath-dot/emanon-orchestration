from __future__ import annotations

import hashlib
import hmac
import json
import math
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Mapping, Sequence, Tuple

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

    def verify_chain(self) -> bool:
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
        return True

    def export_chain(self):
        return [asdict(e) for e in self.chain]


class ThresholdOptimizer:
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
