"use client";

import { createContext, useCallback, useContext, useMemo } from "react";
import { useSession } from "./session";
import type { TermRow } from "./types";
import { useLoad } from "./useLoad";

/**
 * 名称変更（店舗ごとの業務用語の上書き）。
 * - 対象は POS と集計画面の業務用語だけ（キーの一覧はサーバーの apps/core/terms.py）。経理・税務の画面では使わない。
 * - 表示の言い換えだけで、計算・明細の種別は変わらない。伝票の明細に保存済みの名前（取引時点の名前）は変わらない。
 */
type Terms = { rows: TermRow[]; reload: () => Promise<void> };
const TermsContext = createContext<Terms>({ rows: [], reload: async () => {} });

export function TermsProvider({ children }: { children: React.ReactNode }) {
  const { store } = useSession();
  const q = useLoad<{ terms: TermRow[] }>(store ? "/api/store/terms" : null, [store?.id], { refetchOnFocus: false });
  const reload = useCallback(async () => {
    await q.reload(true);
  }, [q]);
  const value = useMemo(() => ({ rows: q.data?.terms ?? [], reload }), [q.data, reload]);
  return <TermsContext.Provider value={value}>{children}</TermsContext.Provider>;
}

export function useTerms(): Terms {
  return useContext(TermsContext);
}

/** 文字列の中の初期値の用語（本指名・場内指名・同伴 など）を、店舗の表示名に置き換える関数を返す。長い用語から順に 1 回で置き換える */
export function useTx(): (text: string) => string {
  const { rows } = useContext(TermsContext);
  return useMemo(() => {
    const pairs = new Map(rows.filter((r) => r.overridden).map((r) => [r.default, r.label]));
    // 画面では「場内指名」を「場内」と短く書いている所があるので、同じ表示名に置き換える
    const jounai = rows.find((r) => r.key === "jounai" && r.overridden);
    if (jounai) pairs.set("場内", jounai.label);
    if (pairs.size === 0) return (text: string) => text;
    const keys = [...pairs.keys()].sort((a, b) => b.length - a.length).map((k) => k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    const re = new RegExp(keys.join("|"), "g");
    return (text: string) => (text ? text.replace(re, (m) => pairs.get(m) ?? m) : text);
  }, [rows]);
}

/** JSX の文字をそのまま言い換える。例: <Tx>本指名</Tx> */
export function Tx({ children }: { children: string }) {
  const tx = useTx();
  return <>{tx(children)}</>;
}
