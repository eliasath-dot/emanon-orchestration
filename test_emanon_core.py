import math

import pytest

from emanon_orchestration_core import (
    ACTION_LEVY_FLIGHT,
    ACTION_PATH_INTEGRATION,
    AuditLogger,
    EmanonMetaController,
    InteractionPolicy,
    ThresholdOptimizer,
)


def test_interaction_policy_known_weight():
    policy = InteractionPolicy()
    assert policy.get_interaction_weight("Resolves") == 2.5


def test_interaction_policy_unknown_weight_raises():
    policy = InteractionPolicy()
    with pytest.raises(ValueError, match="Unknown interaction type"):
        policy.get_interaction_weight("Unknown")


def test_audit_logger_requires_secure_secret():
    with pytest.raises(ValueError, match="secure secret key"):
        AuditLogger("")
    with pytest.raises(ValueError, match="secure secret key"):
        AuditLogger("default_unsafe_key")


def test_audit_logger_record_and_verify_chain():
    logger = AuditLogger("super-secret-key")
    sig1 = logger.record_handshake("agent_alpha", "agent_beta", {"task": "sync"})
    sig2 = logger.record_handshake("agent_beta", "agent_gamma", {"task": "route"})
    assert isinstance(sig1, str)
    assert isinstance(sig2, str)
    assert len(sig1) == 64
    assert len(sig2) == 64
    assert logger.verify_chain() is True


def test_audit_logger_tampered_metadata_fails_verification():
    logger = AuditLogger("super-secret-key")
    logger.record_handshake("agent_alpha", "agent_beta", {"task": "sync"})
    logger.chain[0].metadata["task"] = "tampered"
    assert logger.verify_chain() is False


def test_audit_logger_tampered_signature_fails_verification():
    logger = AuditLogger("super-secret-key")
    logger.record_handshake("agent_alpha", "agent_beta", {"task": "sync"})
    tampered = logger.chain[0]
    logger.chain[0] = type(tampered)(
        timestamp_ns=tampered.timestamp_ns,
        agent_a=tampered.agent_a,
        agent_b=tampered.agent_b,
        metadata=tampered.metadata,
        prev_signature=tampered.prev_signature,
        signature="0" * 64,
    )
    assert logger.verify_chain() is False


def test_audit_logger_prev_signature_tamper_fails_verification():
    logger = AuditLogger("super-secret-key")
    logger.record_handshake("agent_alpha", "agent_beta", {"task": "sync"})
    logger.record_handshake("agent_beta", "agent_gamma", {"task": "route"})
    second = logger.chain[1]
    logger.chain[1] = type(second)(
        timestamp_ns=second.timestamp_ns,
        agent_a=second.agent_a,
        agent_b=second.agent_b,
        metadata=second.metadata,
        prev_signature="f" * 64,
        signature=second.signature,
    )
    assert logger.verify_chain() is False


def test_audit_logger_malformed_entry_fails_verification():
    logger = AuditLogger("super-secret-key")
    logger.record_handshake("agent_alpha", "agent_beta", {"task": "sync"})
    logger.chain[0] = {
        "agent_a": "agent_alpha",
        "agent_b": "agent_beta",
        "metadata": {"task": "sync"},
        "prev_signature": logger.GENESIS_SIGNATURE,
        "signature": "0" * 64,
    }
    assert logger.verify_chain() is False


def test_audit_logger_invalid_metadata_type():
    logger = AuditLogger("super-secret-key")
    with pytest.raises(TypeError, match="Metadata must be a dictionary"):
        logger.record_handshake("agent_alpha", "agent_beta", ["not", "a", "dict"])


def test_audit_logger_unserializable_metadata():
    logger = AuditLogger("super-secret-key")
    with pytest.raises(TypeError, match="JSON-serializable and JSON-safe"):
        logger.record_handshake("agent_alpha", "agent_beta", {"bad": lambda x: x})


def test_audit_logger_nan_metadata_rejected():
    logger = AuditLogger("super-secret-key")
    with pytest.raises(TypeError, match="JSON-serializable and JSON-safe"):
        logger.record_handshake("agent_alpha", "agent_beta", {"score": float("nan")})


def test_audit_logger_invalid_agent_names():
    logger = AuditLogger("super-secret-key")
    with pytest.raises(ValueError, match="agent_a must be a non-empty string"):
        logger.record_handshake("", "agent_beta", {"task": "sync"})
    with pytest.raises(ValueError, match="agent_b must be a non-empty string"):
        logger.record_handshake("agent_alpha", "", {"task": "sync"})


def test_audit_logger_export_chain():
    logger = AuditLogger("super-secret-key")
    logger.record_handshake("agent_alpha", "agent_beta", {"task": "sync"})
    exported = logger.export_chain()
    assert isinstance(exported, list)
    assert len(exported) == 1
    assert exported[0]["agent_a"] == "agent_alpha"
    assert exported[0]["agent_b"] == "agent_beta"
    assert exported[0]["metadata"] == {"task": "sync"}
    assert "timestamp_ns" in exported[0]
    assert "prev_signature" in exported[0]
    assert "signature" in exported[0]


def test_threshold_optimizer_positive_reward():
    optimizer = ThresholdOptimizer()
    result = optimizer.step(clarity=2.0, quality=3.0, v_recycle=1.0, raw_compute_cost=1.0)
    assert result["net_reward"] > 0
    assert result["status"] == "System Aligned: Sustainable Innovation"


def test_threshold_optimizer_zero_reward():
    optimizer = ThresholdOptimizer(waste_weight=1.0, waste_exponent=1.0)
    result = optimizer.step(clarity=1.0, quality=1.0, v_recycle=1.0, raw_compute_cost=2.0)
    assert math.isclose(result["net_reward"], 0.0, abs_tol=1e-9)
    assert result["status"] == "System Balanced: Zero-Cost Coordination"


def test_threshold_optimizer_negative_reward():
    optimizer = ThresholdOptimizer()
    result = optimizer.step(clarity=0.1, quality=0.1, v_recycle=0.0, raw_compute_cost=10.0)
    assert result["net_reward"] < 0
    assert "Circuit Breaker Triggered" in result["status"]


def test_threshold_optimizer_invalid_init_values():
    with pytest.raises(ValueError, match="waste_weight must be finite and non-negative"):
        ThresholdOptimizer(waste_weight=-1.0)
    with pytest.raises(ValueError, match="waste_exponent must be finite and positive"):
        ThresholdOptimizer(waste_exponent=0.0)


def test_threshold_optimizer_invalid_step_inputs():
    optimizer = ThresholdOptimizer()
    with pytest.raises(ValueError, match="must be finite"):
        optimizer.step(float("inf"), 1.0, 1.0, 1.0)
    with pytest.raises(ValueError, match="Compute cost cannot be negative"):
        optimizer.step(1.0, 1.0, 1.0, -0.1)


def test_meta_controller_path_integration():
    controller = EmanonMetaController(root_vector=[1.0, 0.0], drift_threshold=0.75)
    action, similarity = controller.evaluate_trajectory([0.9, 0.1])
    assert action == ACTION_PATH_INTEGRATION
    assert similarity >= 0.75


def test_meta_controller_levy_flight():
    controller = EmanonMetaController(root_vector=[1.0, 0.0], drift_threshold=0.75)
    action, similarity = controller.evaluate_trajectory([0.0, 1.0])
    assert action == ACTION_LEVY_FLIGHT
    assert similarity < 0.75


def test_meta_controller_zero_root_vector_raises():
    with pytest.raises(ValueError, match="root_vector must be non-zero"):
        EmanonMetaController(root_vector=[0.0, 0.0])


def test_meta_controller_dimension_mismatch_raises():
    controller = EmanonMetaController(root_vector=[1.0, 0.0])
    with pytest.raises(ValueError, match="Embedding dimension mismatch"):
        controller.similarity_to_root(__import__("numpy").array([1.0, 0.0, 0.0]))


def test_meta_controller_zero_agent_vector_returns_zero_similarity():
    controller = EmanonMetaController(root_vector=[1.0, 0.0])
    similarity = controller.similarity_to_root(__import__("numpy").array([0.0, 0.0]))
    assert similarity == 0.0


def test_meta_controller_invalid_drift_threshold_raises():
    with pytest.raises(ValueError, match="drift_threshold must be between -1.0 and 1.0"):
        EmanonMetaController(root_vector=[1.0, 0.0], drift_threshold=1.5)


# --- Sealed head: truncation is detectable ---------------------------------

def _chain_of(n, key="truncation-test-key"):
    logger = AuditLogger(key)
    for i in range(n):
        logger.record_handshake("planner", "executor", {"step": i})
    return logger


def test_audit_logger_truncation_undetected_without_head():
    # Documented limit: a chain cut short is still internally consistent.
    logger = _chain_of(5)
    logger.chain.pop()
    assert logger.verify_chain()


def test_audit_logger_truncation_detected_with_head():
    logger = _chain_of(5)
    head = logger.head()
    assert logger.verify_chain(head=head)
    logger.chain.pop()
    logger.last_signature = logger.chain[-1].signature
    assert not logger.verify_chain(head=head)


def test_audit_logger_extension_after_seal_detected():
    logger = _chain_of(3)
    head = logger.head()
    logger.record_handshake("planner", "executor", {"step": "late"})
    assert logger.verify_chain()                 # links are fine
    assert not logger.verify_chain(head=head)    # but the seal says 3, not 4


def test_audit_logger_tampered_head_rejected():
    logger = _chain_of(4)
    head = logger.head()
    head["length"] = 3                           # attacker edits the head to match a cut chain
    logger.chain.pop()
    logger.last_signature = logger.chain[-1].signature
    assert not logger.verify_chain(head=head)    # seal no longer matches
    assert not logger.verify_chain(head={"length": 3})  # malformed head


def test_audit_logger_empty_chain_head():
    logger = AuditLogger("truncation-test-key")
    assert logger.verify_chain(head=logger.head())


# --- Persistence: JSONL round-trip --------------------------------------------

def test_audit_logger_jsonl_round_trip(tmp_path):
    logger = _chain_of(4)
    path = tmp_path / "audit.jsonl"
    logger.save_jsonl(path)
    head = logger.head()
    loaded = AuditLogger.load_jsonl("truncation-test-key", path, head=head)
    assert loaded.export_chain() == logger.export_chain()
    assert loaded.last_signature == logger.last_signature
    # The restored logger can keep recording and the chain stays valid.
    loaded.record_handshake("executor", "reviewer", {"step": "resume"})
    assert loaded.verify_chain()


def test_audit_logger_jsonl_truncated_file_rejected(tmp_path):
    logger = _chain_of(4)
    path = tmp_path / "audit.jsonl"
    logger.save_jsonl(path)
    head = logger.head()
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    AuditLogger.load_jsonl("truncation-test-key", path)          # links still verify
    with pytest.raises(ValueError, match="failed verification"):
        AuditLogger.load_jsonl("truncation-test-key", path, head=head)


def test_audit_logger_jsonl_edited_file_rejected(tmp_path):
    logger = _chain_of(3)
    path = tmp_path / "audit.jsonl"
    logger.save_jsonl(path)
    text = path.read_text(encoding="utf-8").replace('"step":1', '"step":99')
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="failed verification"):
        AuditLogger.load_jsonl("truncation-test-key", path)


def test_audit_logger_jsonl_malformed_line_rejected(tmp_path):
    path = tmp_path / "audit.jsonl"
    path.write_text('{"not": "an entry"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="malformed entry on line 1"):
        AuditLogger.load_jsonl("truncation-test-key", path)


def test_audit_logger_append_jsonl_matches_save(tmp_path):
    logger = AuditLogger("truncation-test-key")
    path = tmp_path / "audit.jsonl"
    for i in range(3):
        logger.record_handshake("a", "b", {"i": i})
        logger.append_jsonl(path, logger.chain[-1])
    loaded = AuditLogger.load_jsonl("truncation-test-key", path, head=logger.head())
    assert len(loaded.chain) == 3
