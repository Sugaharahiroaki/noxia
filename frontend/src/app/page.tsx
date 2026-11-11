"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { useSession } from "@/lib/session";
import { Spinner } from "@/components/ui";

export default function RootPage() {
  const { me, loading, store } = useSession();
  const router = useRouter();
  useEffect(() => {
    if (loading) return;
    if (!me) router.replace("/login");
    else if (!store) router.replace("/select-store");
    else router.replace(store.caps.includes("floor.operate") ? "/floor" : "/attendance");
  }, [me, loading, store, router]);
  return (
    <div className="p-6">
      <Spinner />
    </div>
  );
}
