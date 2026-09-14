import { validLabelKey, validLabelValue } from "./label-values";
import { Button, FormField, Input } from "a13n-ui";
import { XIcon } from "@phosphor-icons/react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router";

export function useLabelFilters(parameter = "label") {
  const [search] = useSearchParams();
  return search.getAll(parameter);
}

export function LabelFilterField({
  parameter = "label",
  label,
}: {
  parameter?: string;
  label?: string;
}) {
  const { t } = useTranslation();
  const [search, setSearch] = useSearchParams();
  const [draft, setDraft] = useState("");
  const values = search.getAll(parameter);
  const apply = (nextValues: string[]) => {
    const next = new URLSearchParams(search);
    next.delete(parameter);
    next.delete("cursor");
    for (const value of [...new Set(nextValues)].sort())
      next.append(parameter, value);
    setSearch(next);
  };
  const separator = draft.indexOf("=");
  const key = separator < 0 ? "" : draft.slice(0, separator);
  const value = separator < 0 ? "" : draft.slice(separator + 1);
  const valid =
    validLabelKey(key) &&
    validLabelValue(value) &&
    !values.some(
      (existing) =>
        existing.slice(0, existing.indexOf("=")) === key && existing !== draft,
    ) &&
    (values.includes(draft) || values.length < 32);
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-2">
      <FormField label={label ?? t("Label filter")} hideLabel className="w-56">
        <Input
          placeholder={t("Label key=value")}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && valid) {
              event.preventDefault();
              apply([...values, draft]);
              setDraft("");
            }
          }}
        />
      </FormField>
      <Button
        variant="outline"
        size="sm"
        type="button"
        disabled={!valid}
        onClick={() => {
          apply([...values, draft]);
          setDraft("");
        }}
      >
        {t("Add filter")}
      </Button>
      {values.length > 0 && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => apply([])}
        >
          {t("Clear filters")}
        </Button>
      )}
      {values.map((value) => (
        <Button
          key={value}
          variant="secondary"
          size="sm"
          type="button"
          aria-label={t("Remove label filter {{value}}", { value })}
          onClick={() => apply(values.filter((item) => item !== value))}
        >
          {value} <XIcon size={12} />
        </Button>
      ))}
    </div>
  );
}
