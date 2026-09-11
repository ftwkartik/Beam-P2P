/**
 * Minimal channel shapes sender.ts and receiver.ts depend on, satisfied structurally
 * by a real `RTCDataChannel` with no adapter needed at the call site -- and by simple
 * fakes in tests, since jsdom has no RTCDataChannel at all.
 */

export interface ControlChannelLike {
  send(data: string): void;
}

export interface DataChannelLike {
  send(data: Uint8Array): void;
  readonly bufferedAmount: number;
  addEventListener(type: "bufferedamountlow", listener: () => void): void;
  removeEventListener(type: "bufferedamountlow", listener: () => void): void;
}
