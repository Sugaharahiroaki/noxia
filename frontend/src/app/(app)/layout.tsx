"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import AppShell from "@/components/AppShell";
import { Spinner } from "@/components/ui";
import { useSession } from "@/lib/session";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { me, loading, store, storeEpoch } = useSession();
  const router = useRouter();
  const pathname = usePathname();
  const needsStore = !pathname.startsWith("/select-store") && !pathname.startsWith("/platform") && !pathname.startsWith("/company");

  useEffect(() => {
    if (loading) return;
    if (!me) router.replace("/login");
    else if (needsStore && !store) router.replace("/select-store");
  }, [me, loading, store, needsStore, router]);

  if (loading || !me || (needsStore && !store)) {
    return (
      <div className="p-6">
        <Spinner />
      </div>
    );
  }
  // storeEpoch を key にして、店舗切替時に配下を丸ごと再マウントする（前の店舗の情報を残さない）
  return <AppShell key={storeEpoch}>{children}</AppShell>;
}
