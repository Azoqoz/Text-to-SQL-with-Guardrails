import { ApiError, getCapabilities, getHealth } from "./api";
import type { Role } from "./types";

export const WAKE_WINDOW_MS = 90_000;
export const HEALTH_RETRY_MS = 2_500;

function pause(ms: number, signal: AbortSignal): Promise<void> {
  signal.throwIfAborted();
  return new Promise((resolve, reject) => {
    const finish = () => { signal.removeEventListener("abort", cancel); resolve(); };
    const timer = setTimeout(finish, ms);
    const cancel = () => { clearTimeout(timer); reject(signal.reason); };
    signal.addEventListener("abort", cancel, { once: true });
  });
}

async function waitForHealth(signal: AbortSignal): Promise<void> {
  signal.throwIfAborted();
  const windowController = new AbortController();
  const wakeSignal = AbortSignal.any([signal, windowController.signal]);
  const deadline = setTimeout(() => windowController.abort(), WAKE_WINDOW_MS);
  try {
    while (true) {
      wakeSignal.throwIfAborted();
      const started = Date.now();
      const attempt = new AbortController();
      const timeout = setTimeout(() => attempt.abort(), HEALTH_RETRY_MS);
      try {
        await getHealth(AbortSignal.any([wakeSignal, attempt.signal]));
        wakeSignal.throwIfAborted();
        return;
      } catch {
        // Cold starts can return network failures, timeouts, or proxy error pages.
        // All are retried within the same bounded window, without showing an alert.
      } finally {
        clearTimeout(timeout);
      }
      wakeSignal.throwIfAborted();
      // Await each probe before starting another. Slow probes consume this delay.
      await pause(Math.max(0, HEALTH_RETRY_MS - (Date.now() - started)), wakeSignal);
    }
  } catch {
    signal.throwIfAborted();
    throw new ApiError("offline", "The API did not become ready within 90 seconds. Please reconnect to try again.");
  } finally {
    clearTimeout(deadline);
  }
}

export async function loadReadyCapabilities(
  signal: AbortSignal, role?: Role, employeeId?: number, onHealthy?: () => void,
) {
  await waitForHealth(signal);
  signal.throwIfAborted();
  onHealthy?.();
  return getCapabilities(signal, role, employeeId);
}
