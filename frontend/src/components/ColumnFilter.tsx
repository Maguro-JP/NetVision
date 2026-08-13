import { useEffect, useId, useMemo, useRef, useState } from "react";

import { SEARCH_DELAY_MS, useDebouncedValue } from "../lib/debounce";

/**
 * 列見出しの下に置く、Excel のフィルターと同じ場所の入力。
 *
 * 打った文字でその列を絞り込む。選択肢がある列は、打つとその場で候補が
 * 絞られる（一覧を目で探す必要がない）。
 */

export type FilterChoice = { value: string; label: string };

/** 自由入力の列。落ち着いてから親へ渡す。 */
export function ColumnTextFilter({
  value,
  onChange,
  placeholder = "含む…",
  title,
}: {
  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
  title?: string;
}) {
  const [draft, setDraft] = useState(value);
  const debounced = useDebouncedValue(draft, SEARCH_DELAY_MS);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    setDraft(value);
  }, [value]);

  useEffect(() => {
    if (debounced !== value) onChangeRef.current(debounced);
  }, [debounced, value]);

  return (
    <input
      type="search"
      className="column-filter-input"
      value={draft}
      placeholder={placeholder}
      title={title ?? placeholder}
      onChange={(e) => setDraft(e.target.value)}
      aria-label={title ?? placeholder}
    />
  );
}

/**
 * 決まった候補から選ぶ列。入力欄に打つと候補が絞られる。
 * 選んだあとも入力欄に表示名を残すので、今なにで絞っているか分かる。
 */
export function ColumnChoiceFilter({
  value,
  onChange,
  choices,
  allLabel = "すべて",
  placeholder = "探す…",
  title,
}: {
  value: string;
  onChange: (next: string) => void;
  choices: FilterChoice[];
  allLabel?: string;
  placeholder?: string;
  title?: string;
}) {
  const listId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const selected = choices.find((c) => c.value === value);
  const [open, setOpen] = useState(false);
  const [needle, setNeedle] = useState("");

  useEffect(() => {
    if (!open) setNeedle("");
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const visible = useMemo(() => {
    const folded = needle.trim().toLowerCase();
    if (!folded) return choices;
    return choices.filter(
      (c) => c.label.toLowerCase().includes(folded) || c.value.toLowerCase().includes(folded),
    );
  }, [choices, needle]);

  const display = open ? needle : (selected?.label ?? "");

  return (
    <div className={`column-choice ${open ? "open" : ""}`} ref={rootRef}>
      <input
        type="search"
        className="column-filter-input"
        value={display}
        placeholder={selected ? selected.label : placeholder}
        title={title ?? placeholder}
        aria-label={title ?? placeholder}
        aria-expanded={open}
        aria-controls={listId}
        onFocus={() => setOpen(true)}
        onClick={() => setOpen(true)}
        onChange={(e) => {
          setOpen(true);
          setNeedle(e.target.value);
        }}
      />
      {open && (
        <ul id={listId} className="column-choice-list" role="listbox">
          <li>
            <button
              type="button"
              className={!value ? "active" : ""}
              onClick={() => {
                onChange("");
                setOpen(false);
              }}
            >
              {allLabel}
            </button>
          </li>
          {visible.map((choice) => (
            <li key={choice.value}>
              <button
                type="button"
                className={choice.value === value ? "active" : ""}
                onClick={() => {
                  onChange(choice.value);
                  setOpen(false);
                }}
              >
                {choice.label}
              </button>
            </li>
          ))}
          {visible.length === 0 && <li className="column-choice-empty">該当なし</li>}
        </ul>
      )}
    </div>
  );
}
