export type ErrorCode =
  | "AuthenticationError" | "AuthorizationError" | "ConnectionError"
  | "TimeoutError" | "RateLimitError" | "InvalidRequestError"
  | "UnsupportedCapabilityError" | "ModelNotFoundError" | "ProviderError"
  | "ProtocolError" | "CancelledError";

export interface ErrorDetails {
  statusCode?: number;
  providerCode?: string;
  requestId?: string;
  providerDetails?: { message?: string; type?: string; code?: string };
  cause?: { name: string; message: string; code?: string };
}

export class ConduitError extends Error {
  declare name: ErrorCode;
  declare statusCode?: number;
  declare providerCode?: string;
  declare requestId?: string;
  declare providerDetails?: ErrorDetails["providerDetails"];
  declare cause?: ErrorDetails["cause"];

  constructor(name: ErrorCode, message: string, details: ErrorDetails = {}) {
    super(message);
    this.name = name;
    Object.assign(this, details);
  }
}
