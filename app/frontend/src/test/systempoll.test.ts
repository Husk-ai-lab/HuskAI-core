import { describe, expect, it } from "vitest";
import { SYSTEM_POLL_MS, systemPollMs } from "../lib/hooks";

describe("system status polling", () => {
  it("asks often until Studio shows up, then relaxes", () => {
    expect(systemPollMs(undefined)).toBe(SYSTEM_POLL_MS.studioDown);
    expect(systemPollMs({ studio: { alive: false } } as never)).toBe(SYSTEM_POLL_MS.studioDown);
    expect(systemPollMs({ studio: { alive: true } } as never)).toBe(SYSTEM_POLL_MS.studioUp);
    expect(SYSTEM_POLL_MS.studioDown).toBeLessThan(SYSTEM_POLL_MS.studioUp);
  });
});
