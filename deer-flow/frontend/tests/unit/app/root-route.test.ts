import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "@rstest/core";

const FRONTEND_ROOT = path.resolve(__dirname, "../../..");

describe("root route", () => {
  it("redirects to the crawler agent and preserves it across login", () => {
    const source = readFileSync(
      path.join(FRONTEND_ROOT, "src/app/page.tsx"),
      "utf8",
    );

    expect(source).toContain(
      'const DEFAULT_CRAWLER_PATH = "/workspace/agents/crawler-lead/chats/new"',
    );
    expect(source).toContain("buildLoginUrl(DEFAULT_CRAWLER_PATH)");
    expect(source).not.toContain("components/landing");
  });
});
