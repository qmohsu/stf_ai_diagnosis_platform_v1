export class PortalError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "PortalError";
  }
}

export function errorId(prefix: string): string {
  return `${prefix}-${crypto.randomUUID().slice(0, 8).toUpperCase()}`;
}
