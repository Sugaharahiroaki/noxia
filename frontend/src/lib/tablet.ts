"use client";

import { useMemo } from "react";
import { useSession } from "./session";

/**
 * 店舗タブレット用アカウントの画面の出し分け。
 * - is_tablet のアカウントは、店舗設定「権限」のタブレットの列でチェックした画面（tablet_menu のキー = URL）だけをメニューに出し、
 *   それ以外の URL を開いたら卓一覧へ戻す。経理の画面はパソコンだけ。
 * - これは画面の出し分け。できる操作（API の権限）は同じ表の「タブレットでできる操作」で決まり、サーバーが caps に入れて返す。
 */
/** キーを許可したときに、いっしょに開けるようにする画面（卓一覧 → 卓詳細・注文・会計 など） */
const IMPLIED: Record<string, string[]> = {
  // 会計のあとの「手書き領収書の金額」（/checkouts/[id]/handwrite）で、領収書を渡した記録も付ける
  "/floor": ["/visits", "/checkouts"],
  "/visits/new": ["/visits"],
  "/sales": ["/checkouts"],
  "/receipts": ["/receipts"],
};
/** 常に開ける（ログイン・店舗選択） */
const ALWAYS = ["/", "/login", "/select-store", "/invite"];

export type TabletMenu = {
  /** タブレット用アカウントか */
  tablet: boolean;
  keys: string[];
  /** メニューの項目（URL）を出すか */
  allows: (href: string) => boolean;
  /** その URL を開いてよいか */
  allowsPath: (pathname: string) => boolean;
};

export function useTabletMenu(): TabletMenu {
  const { store } = useSession();
  const tablet = !!store?.is_tablet;
  const joined = (store?.tablet_menu ?? []).join("|");
  return useMemo(() => {
    const keys = joined ? joined.split("|") : [];
    const prefixes = keys.flatMap((k) => [k, ...(IMPLIED[k] ?? [])]);
    const hit = (pathname: string, p: string) => pathname === p || pathname.startsWith(`${p}/`);
    return {
      tablet,
      keys,
      allows: (href: string) => !tablet || keys.includes(href),
      allowsPath: (pathname: string) => !tablet || ALWAYS.includes(pathname) || prefixes.some((p) => hit(pathname, p)),
    };
  }, [tablet, joined]);
}
