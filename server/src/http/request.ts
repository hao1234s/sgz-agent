import type { Request } from "express";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}
export const fail = (status: number, message: string): never => {
  throw new ApiError(status, message);
};
export function body(req: Request, allowed: string[]): Record<string, unknown> {
  const value = req.body;
  if (
    !value ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    Object.keys(value).some((key) => !allowed.includes(key))
  )
    return fail(400, "Invalid request body");
  return value;
}
export function validate(action: () => void) {
  try {
    action();
  } catch (error) {
    fail(400, (error as Error).message);
  }
}
