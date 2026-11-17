/** お客様に渡した領収書の記録（apps.sales の ReceiptIssue）。金額は文字列の Decimal。経費の領収書（types_expenses）とは別物。 */
import type { Money } from "./types";

export type ReceiptIssue = {
  id: number;
  checkout: number;
  method: "handwritten" | "printed";
  method_label: string;
  /** 発行した時点の領収額（現金＋カード。未収は含めない） */
  amount: Money;
  addressee: string;
  proviso: string;
  /** 2 回目以降（領収書に「再発行」と書いたもの） */
  reissue: boolean;
  issued_at: string;
  issued_by_name: string;
  /** 打ち間違いとして取り消した記録（一覧には残るが、発行済みには数えない） */
  cancelled_at: string | null;
  cancelled_by_name: string;
};

/** 会計（Checkout・手書き領収書の転記用）に付く領収書の情報 */
export type ReceiptInfo = {
  /** 領収額（現金＋カード）。0 なら全額ツケで、領収書は出さない */
  receipt_amount: Money;
  /** 発行の記録（取り消したものも含む。古い順） */
  receipt_issues: ReceiptIssue[];
};

/** 取り消していない記録だけ（発行済みかどうかはこれで見る） */
export function activeIssues(info: Partial<ReceiptInfo> | null | undefined): ReceiptIssue[] {
  return (info?.receipt_issues ?? []).filter((r) => !r.cancelled_at);
}
