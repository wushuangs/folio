"use client";

import { useEffect, useId, useRef, useState } from "react";
import { Check, ChevronDown } from "lucide-react";

type Option = { value: string; label: string };
export function Select({
  label,
  value,
  options,
  disabled = false,
  onChange,
}: {
  label: string;
  value: string;
  options: Option[];
  disabled?: boolean;
  onChange: (value: string) => void;
}) {
  const id = useId();
  const root = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const selected = Math.max(
    0,
    options.findIndex((option) => option.value === value),
  );
  const expanded = open && !disabled;
  useEffect(() => {
    if (!expanded) return;
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [expanded]);
  function choose(index: number) {
    if (disabled || !options[index]) return;
    onChange(options[index].value);
    setOpen(false);
  }
  return (
    <div className="folio-select" ref={root}>
      <button
        type="button"
        className="folio-select-trigger"
        role="combobox"
        aria-label={label}
        aria-haspopup="listbox"
        aria-expanded={expanded}
        aria-controls={`${id}-list`}
        aria-activedescendant={expanded ? `${id}-${active}` : undefined}
        disabled={disabled}
        onClick={() => {
          setActive(selected);
          setOpen(!expanded);
        }}
        onBlur={(event) => {
          if (!root.current?.contains(event.relatedTarget as Node))
            setOpen(false);
        }}
        onKeyDown={(event) => {
          if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
            event.preventDefault();
            setOpen(true);
            setActive(
              event.key === "Home"
                ? 0
                : event.key === "End"
                  ? options.length - 1
                  : !expanded
                    ? selected
                    : (active +
                        (event.key === "ArrowDown" ? 1 : -1) +
                        options.length) %
                      options.length,
            );
          } else if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            if (expanded) choose(active);
            else {
              setActive(selected);
              setOpen(true);
            }
          } else if (event.key === "Escape" || event.key === "Tab") {
            setOpen(false);
          } else if (event.key.length === 1) {
            const index = options.findIndex((option) =>
              option.label.toLowerCase().startsWith(event.key.toLowerCase()),
            );
            if (index >= 0) {
              setActive(index);
              setOpen(true);
            }
          }
        }}
      >
        <span>{options[selected]?.label}</span>
        <ChevronDown size={14} />
      </button>
      {expanded && (
        <div
          id={`${id}-list`}
          role="listbox"
          aria-label={label}
          className="folio-select-menu"
        >
          {options.map((option, index) => (
            <div
              id={`${id}-${index}`}
              role="option"
              aria-selected={value === option.value}
              className={active === index ? "highlighted" : ""}
              key={option.value}
              onPointerMove={() => setActive(index)}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => choose(index)}
            >
              <span>{option.label}</span>
              {value === option.value && <Check size={14} />}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
