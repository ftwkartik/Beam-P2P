"""Signaling protocol: JSON messages exchanged over the `/ws` WebSocket between a
client (browser or CLI) and the server (see docs/protocol.md §3).

Every message is a Pydantic model discriminated by its `type` field, with
`extra="forbid"` so an unrecognized field is a validation error rather than being
silently ignored -- this is the "Pydantic discriminated-union protocol" security.md
calls for as a defense against malformed or exploratory client input.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from beam_protocol.constants import MAX_ICE_CANDIDATE_BYTES, MAX_SDP_BYTES, PROTOCOL_VERSION

Role = Literal["creator", "joiner"]
PeerState = Literal["online", "reconnecting"]
PeerLeftReason = Literal["left", "timeout", "closed"]
ClientKind = Literal["web", "cli"]
SdpType = Literal["offer", "answer", "pranswer", "rollback"]


class _StrictModel(BaseModel):
    """Base for every wire message: unknown fields are rejected, not ignored."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def _check_utf8_size(value: str, limit: int, what: str) -> str:
    size = len(value.encode("utf-8"))
    if size > limit:
        raise ValueError(f"{what} is {size} bytes, over the {limit}-byte limit")
    return value


# --- Signal payloads (carried inside a `signal` message) -------------------------


class SdpDescription(_StrictModel):
    type: SdpType
    sdp: str

    @field_validator("sdp")
    @classmethod
    def _limit_sdp_size(cls, value: str) -> str:
        return _check_utf8_size(value, MAX_SDP_BYTES, "sdp")


class SdpSignal(_StrictModel):
    kind: Literal["description"] = "description"
    description: SdpDescription


class IceCandidateData(_StrictModel):
    candidate: str
    sdpMid: str | None = None
    sdpMLineIndex: int | None = None
    usernameFragment: str | None = None

    @field_validator("candidate")
    @classmethod
    def _limit_candidate_size(cls, value: str) -> str:
        return _check_utf8_size(value, MAX_ICE_CANDIDATE_BYTES, "candidate")


class IceSignal(_StrictModel):
    kind: Literal["candidate"] = "candidate"
    #: `None` signals end-of-candidates (see docs/protocol.md §3).
    candidate: IceCandidateData | None = None


SignalData = Annotated[SdpSignal | IceSignal, Field(discriminator="kind")]


# --- Client -> server --------------------------------------------------------------


class ClientInfo(_StrictModel):
    kind: ClientKind
    version: str


class HelloMessage(_StrictModel):
    type: Literal["hello"] = "hello"
    v: int = PROTOCOL_VERSION
    token: str
    client: ClientInfo


class ClientSignalMessage(_StrictModel):
    type: Literal["signal"] = "signal"
    data: SignalData


class LeaveMessage(_StrictModel):
    type: Literal["leave"] = "leave"


ClientMessage = Annotated[
    HelloMessage | ClientSignalMessage | LeaveMessage,
    Field(discriminator="type"),
]


# --- Server -> client --------------------------------------------------------------


class PeerInfo(_StrictModel):
    peer_id: str
    role: Role
    state: PeerState


class WelcomeMessage(_StrictModel):
    type: Literal["welcome"] = "welcome"
    peer_id: str
    room_id: str
    role: Role
    polite: bool
    peers: list[PeerInfo]
    expires_at: str


class PeerJoinedMessage(_StrictModel):
    type: Literal["peer_joined"] = "peer_joined"
    peer_id: str
    role: Role


class PeerReconnectingMessage(_StrictModel):
    type: Literal["peer_reconnecting"] = "peer_reconnecting"
    peer_id: str


class PeerLeftMessage(_StrictModel):
    type: Literal["peer_left"] = "peer_left"
    peer_id: str
    reason: PeerLeftReason


class ServerSignalMessage(_StrictModel):
    type: Literal["signal"] = "signal"
    from_: str = Field(alias="from")
    data: SignalData


class ServerErrorMessage(_StrictModel):
    type: Literal["error"] = "error"
    code: str
    message: str


ServerMessage = Annotated[
    WelcomeMessage
    | PeerJoinedMessage
    | PeerReconnectingMessage
    | PeerLeftMessage
    | ServerSignalMessage
    | ServerErrorMessage,
    Field(discriminator="type"),
]
