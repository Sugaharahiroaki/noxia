"use client";

import { useMemo } from "react";
import { useSession } from "./session";

/**
 * 店舗タブレット用アカウントの画面の出し分け。
 * - is_tablet のアカウントは、店舗設定「タブレット」で選んだ画面（tablet_menu のキー = URL）だけをメニューに出し、
 *   それ以外の URL を開いたら卓一覧へ戻す。管理・経理・設定は PC から（オーナーの方針）。
 * - これは表示の出し分けだけ。API の権限は役割（ケイパビリティ）で決まる。
 */
/** キーを許可したときに、いっしょに開けるようにする画面（卓一覧 → 卓詳細・注文・会計 など） */
const IMPLIED: Record<string, string[]> = {
  "/floor": ["/visits"],
  "/visits/new": ["/visits"],
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
