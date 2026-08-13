"""Signaling message validation (docs/protocol.md §3)."""

import pytest
from pydantic import TypeAdapter, ValidationError

from beam_protocol.constants import MAX_ICE_CANDIDATE_BYTES, MAX_SDP_BYTES
from beam_protocol.signaling import (
    ClientMessage,
    HelloMessage,
    IceSignal,
    LeaveMessage,
    SdpSignal,
    ServerMessage,
    ServerSignalMessage,
    WelcomeMessage,
)

client_adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)
server_adapter: TypeAdapter[ServerMessage] = TypeAdapter(ServerMessage)


def test_hello_message_round_trips() -> None:
    msg = HelloMessage(token="tok123", client={"kind": "web", "version": "0.1.0"})  # type: ignore[arg-type]
    data = msg.model_dump(mode="json")
    assert data["type"] == "hello"
    parsed = client_adapter.validate_python(data)
    assert isinstance(parsed, HelloMessage)
    assert parsed.token == "tok123"


def test_leave_message_has_no_extra_fields_allowed() -> None:
    with pytest.raises(ValidationError):
        LeaveMessage.model_validate({"type": "leave", "extra": "nope"})


def test_client_message_discriminates_on_type() -> None:
    parsed = client_adapter.validate_python({"type": "leave"})
    assert isinstance(parsed, LeaveMessage)


def test_unknown_message_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        client_adapter.validate_python({"type": "not-a-real-type"})


def test_sdp_signal_over_size_limit_is_rejected() -> None:
    huge_sdp = "a" * (MAX_SDP_BYTES + 1)
    with pytest.raises(ValidationError, match="sdp"):
        SdpSignal(description={"type": "offer", "sdp": huge_sdp})  # type: ignore[arg-type]


def test_sdp_signal_at_size_limit_is_accepted() -> None:
    sdp = "a" * MAX_SDP_BYTES
    signal = SdpSignal(description={"type": "offer", "sdp": sdp})  # type: ignore[arg-type]
    assert len(signal.description.sdp) == MAX_SDP_BYTES


def test_ice_candidate_over_size_limit_is_rejected() -> None:
    huge_candidate = "a" * (MAX_ICE_CANDIDATE_BYTES + 1)
    with pytest.raises(ValidationError, match="candidate"):
        IceSignal(candidate={"candidate": huge_candidate})  # type: ignore[arg-type]


def test_ice_signal_end_of_candidates_is_none() -> None:
    signal = IceSignal(candidate=None)
    assert signal.candidate is None


def test_signal_data_discriminates_on_kind() -> None:
    sdp = SdpSignal.model_validate(
        {"kind": "description", "description": {"type": "offer", "sdp": "v=0"}}
    )
    assert isinstance(sdp, SdpSignal)


def test_server_signal_message_uses_from_alias() -> None:
    msg = ServerSignalMessage.model_validate(
        {
            "type": "signal",
            "from": "peer-123",
            "data": {"kind": "candidate", "candidate": None},
        }
    )
    assert msg.from_ == "peer-123"
    # Serialization must round-trip through the *wire* alias ("from"), not the
    # Python-safe attribute name ("from_"), since "from" is a reserved keyword.
    assert msg.model_dump(mode="json", by_alias=True)["from"] == "peer-123"


def test_welcome_message_structure() -> None:
    msg = WelcomeMessage(
        peer_id="p1",
        room_id="r1",
        role="creator",
        polite=False,
        peers=[{"peer_id": "p2", "role": "joiner", "state": "online"}],  # type: ignore[list-item]
        expires_at="2026-01-01T00:00:00Z",
    )
    parsed = server_adapter.validate_python(msg.model_dump(mode="json"))
    assert isinstance(parsed, WelcomeMessage)
    assert parsed.peers[0].peer_id == "p2"
