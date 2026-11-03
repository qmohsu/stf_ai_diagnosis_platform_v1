import "@testing-library/jest-dom/vitest";
import { vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: vi.fn(), replace: vi.fn(), push: vi.fn(), back: vi.fn() }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(),
  notFound: vi.fn(),
}));

if (!("BroadcastChannel" in globalThis)) {
  class TestBroadcastChannel {
    onmessage: ((event: MessageEvent) => void) | null = null;
    constructor(public readonly name: string) {}
    postMessage() {}
    close() {}
  }
  Object.defineProperty(globalThis, "BroadcastChannel", { value: TestBroadcastChannel, configurable: true });
}
