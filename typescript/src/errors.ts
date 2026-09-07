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

// HTTP categories are shared; each wire parser supplies already-sanitized details.
export function httpFailure(status: number, details: NonNullable<ErrorDetails["providerDetails"]>, requestId?: string, modelNotFound = false): ConduitError {
  const codes: Record<number, ErrorCode> = { 400: "InvalidRequestError", 401: "AuthenticationError", 403: "AuthorizationError", 408: "TimeoutError", 422: "InvalidRequestError", 429: "RateLimitError" };
  const name = status === 404 && modelNotFound ? "ModelNotFoundError" : codes[status] ?? "ProviderError";
  return new ConduitError(name, details.message || `Provider returned HTTP ${status}.`, {
    statusCode: status,
    ...(requestId !== undefined && { requestId }),
    ...(details.code !== undefined && { providerCode: details.code }),
    ...(Object.keys(details).length > 0 && { providerDetails: details }),
  });
}
