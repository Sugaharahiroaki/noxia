"use client";

/**
 * PC の一覧・承認・照合用の表。ページ送り・複数選択・キーボード（j/k で移動、Space で選択）。
 */
import { useEffect, useMemo, useState } from "react";

export type Column<T> = {
  key: string;
  label: string;
  render?: (row: T) => React.ReactNode;
  align?: "left" | "right" | "center";
  width?: number | string;
};

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  selectable,
  selected,
  onSelectedChange,
  onRowClick,
  activeKey,
  emptyText = "該当するものはありません。",
  page,
  pageSize,
  total,
  onPageChange,
  keyboard,
  rowClassName,
}: {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string | number;
  selectable?: boolean;
  selected?: Set<string | number>;
  onSelectedChange?: (s: Set<string | number>) => void;
  onRowClick?: (row: T) => void;
  activeKey?: string | number | null;
  emptyText?: string;
  page?: number;
  pageSize?: number;
  total?: number;
  onPageChange?: (page: number) => void;
  keyboard?: boolean;
  rowClassName?: (row: T) => string;
}) {
  const [cursor, setCursor] = useState(0);
  const keys = useMemo(() => rows.map(rowKey), [rows, rowKey]);
  useEffect(() => {
    if (cursor >= rows.length) setCursor(Math.max(0, rows.length - 1));
  }, [rows.length, cursor]);
  useEffect(() => {
    if (!keyboard) return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
      if (document.querySelector('[role="dialog"]')) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === "j" || e.key === "ArrowDown") {
        setCursor((c) => Math.min(rows.length - 1, c + 1));
        e.preventDefault();
      } else if (e.key === "k" || e.key === "ArrowUp") {
        setCursor((c) => Math.max(0, c - 1));
        e.preventDefault();
      } else if (e.key === " " && selectable && onSelectedChange && rows[cursor]) {
        const k = keys[cursor];
        const next = new Set(selected ?? []);
        if (next.has(k)) next.delete(k);
        else next.add(k);
        onSelectedChange(next);
        e.preventDefault();
      } else if (e.key === "Enter" && onRowClick && rows[cursor]) {
        onRowClick(rows[cursor]);
        e.preventDefault();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [keyboard, rows, cursor, keys, selectable, selected, onSelectedChange, onRowClick]);

  const allSelected = selectable && rows.length > 0 && rows.every((r) => selected?.has(rowKey(r)));
  const pages = total && pageSize ? Math.max(1, Math.ceil(total / pageSize)) : 1;

  return (
    <div>
      <div className="scroll-x" style={{ overflowX: "auto" }}>
        <table className="table">
          <thead>
            <tr>
              {selectable && (
                <th style={{ width: 48 }}>
                  <input
                    type="checkbox"
                    aria-label="すべて選択"
                    checked={!!allSelected}
                    onChange={(e) => {
                      const next = new Set(selected ?? []);
                      rows.forEach((r) => (e.target.checked ? next.add(rowKey(r)) : next.delete(rowKey(r))));
                      onSelectedChange?.(next);
                    }}
                    style={{ width: 22, height: 22 }}
                  />
                </th>
              )}
              {columns.map((c) => (
                <th key={c.key} style={{ textAlign: c.align ?? "left", width: c.width }}>
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={columns.length + (selectable ? 1 : 0)} className="text-center py-8" style={{ color: "var(--color-muted)" }}>
                  {emptyText}
                </td>
              </tr>
            )}
            {rows.map((r, i) => {
              const k = rowKey(r);
              const isActive = activeKey === k || (keyboard && i === cursor);
              const isSel = selected?.has(k);
              return (
                <tr
                  key={k}
                  onClick={() => {
                    setCursor(i);
                    onRowClick?.(r);
                  }}
                  className={rowClassName?.(r)}
                  style={{
                    background: isSel ? "rgba(91,147,255,0.12)" : isActive ? "var(--color-panel2)" : undefined,
                    outline: keyboard && i === cursor ? "2px solid var(--color-accent)" : undefined,
                    cursor: onRowClick ? "pointer" : undefined,
                  }}
                >
                  {selectable && (
                    <td onClick={(e) => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        aria-label="選択"
                        checked={!!isSel}
                        onChange={(e) => {
                          const next = new Set(selected ?? []);
                          if (e.target.checked) next.add(k);
                          else next.delete(k);
                          onSelectedChange?.(next);
                        }}
                        style={{ width: 22, height: 22 }}
                      />
                    </td>
                  )}
                  {columns.map((c) => (
                    <td key={c.key} style={{ textAlign: c.align ?? "left" }} className={c.align === "right" ? "num" : undefined}>
                      {c.render ? c.render(r) : String((r as Record<string, unknown>)[c.key] ?? "")}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {pages > 1 && page && onPageChange && (
        <div className="flex items-center justify-between mt-3">
          <button className="btn btn-sm" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>
            ← 前の{pageSize}件
          </button>
          <span className="text-sm" style={{ color: "var(--color-muted)" }}>
            {page} / {pages} ページ（全 {total} 件）
          </span>
          <button className="btn btn-sm" disabled={page >= pages} onClick={() => onPageChange(page + 1)}>
            次の{pageSize}件 →
          </button>
        </div>
      )}
      {keyboard && (
        <p className="kbd-hint text-sm mt-2" style={{ color: "var(--color-muted)" }}>
          キーボード: j / k で移動{selectable ? " ・ Space で選択" : ""}{onRowClick ? " ・ Enter で開く" : ""}
        </p>
      )}
    </div>
  );
}
