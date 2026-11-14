import { redirect } from "next/navigation";

import { getServerSideUser } from "@/core/auth/server";
import { assertNever, buildLoginUrl } from "@/core/auth/types";

const DEFAULT_CRAWLER_PATH = "/workspace/agents/crawler-lead/chats/new";

export const dynamic = "force-dynamic";

export default async function RootPage() {
  const auth = await getServerSideUser();
  switch (auth.tag) {
    case "authenticated":
    case "gateway_unavailable":
      redirect(DEFAULT_CRAWLER_PATH);
    case "unauthenticated":
      redirect(buildLoginUrl(DEFAULT_CRAWLER_PATH));
    case "needs_setup":
    case "system_setup_required":
      redirect("/setup");
    case "config_error":
      throw new Error(auth.message);
    default:
      assertNever(auth);
  }
}
