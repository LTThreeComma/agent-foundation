import { validLabelKey, validLabelValue } from "./label-values";
import { Button, Input } from "a13n-ui";
import { PencilSimpleIcon, TrashIcon } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ErrorNotice } from "./feedback";

type Labels = Record<string, string>;
type LabelRow = [string, string];
type LabelsRepresentation = { value: { labels: Labels }; etag?: string };

export function ResourceLabelsPanel({
  resourceId,
  labels,
  editable,
  read,
  write,
  onSaved,
}: {
  resourceId: string;
  labels?: Labels;
  editable: boolean;
  read: (signal: AbortSignal) => Promise<LabelsRepresentation>;
  write: (labels: Labels, etag: string) => Promise<unknown>;
  onSaved?: () => void;
}) {
  const { t } = useTranslation();
  const cache = useQueryClient();
  const [draft, setDraft] = useState<LabelRow[] | null>(null);
  const [baseline, setBaseline] = useState<LabelsRepresentation>();
  const query = useQuery({
    queryKey: ["resource-labels", resourceId],
    queryFn: ({ signal }) => read(signal),
    enabled: labels === undefined,
  });
  const edit = async () => {
    mutation.reset();
    const result = await query.refetch();
    if (result.isSuccess) {
      setBaseline(result.data);
      setDraft(Object.entries(result.data.value.labels));
    }
  };
  const refresh = async () => {
    const result = await query.refetch();
    if (result.isSuccess) {
      // Refresh the precondition, retaining the user's draft after a conflict.
      setBaseline(result.data);
      mutation.reset();
    }
  };
  const mutation = useMutation({
    mutationFn: async () => {
      if (!baseline?.etag || draft === null)
        throw new Error(
          t("Version information is unavailable. Reload this page."),
        );
      const next = Object.fromEntries(draft);
      await write(next, baseline.etag);
      return next;
    },
    onSuccess: (next) => {
      const updated = { value: { labels: next } };
      cache.setQueryData(["resource-labels", resourceId], updated);
      setBaseline(updated);
      setDraft(null);
      onSaved?.();
    },
  });
  const current =
    baseline?.value.labels ?? labels ?? query.data?.value.labels ?? {};
  const validation = draft === null ? undefined : validateRows(draft);
  return (
    <section
      className="mt-1 space-y-2 border-t border-border pt-2"
      aria-label={t("Labels")}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground">{t("Labels")}</span>
        {editable && draft === null && (
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label={t("Edit labels")}
            disabled={query.isFetching}
            onClick={() => void edit()}
          >
            <PencilSimpleIcon size={14} />
          </Button>
        )}
      </div>
      {draft === null ? (
        labels === undefined && query.isPending ? (
          <p className="text-xs text-muted-foreground">
            {t("Loading labels…")}
          </p>
        ) : (
          <LabelPreview labels={current} />
        )
      ) : (
        <div className="space-y-2">
          <div className="max-h-64 space-y-2 overflow-y-auto">
            {draft.map(([key, value], index) => (
              <div
                className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] items-center gap-1"
                key={index}
              >
                <Input
                  aria-label={t("Label key")}
                  value={key}
                  maxLength={63}
                  disabled={mutation.isPending}
                  onChange={(event) =>
                    setDraft(
                      draft.map((row, i) =>
                        i === index ? [event.target.value, value] : row,
                      ),
                    )
                  }
                />
                <Input
                  aria-label={t("Label value")}
                  value={value}
                  disabled={mutation.isPending}
                  onChange={(event) =>
                    setDraft(
                      draft.map((row, i) =>
                        i === index ? [key, event.target.value] : row,
                      ),
                    )
                  }
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  aria-label={t("Remove label")}
                  disabled={mutation.isPending}
                  onClick={() => setDraft(draft.filter((_, i) => i !== index))}
                >
                  <TrashIcon className="text-destructive" size={14} />
                </Button>
              </div>
            ))}
          </div>
          <div className="flex gap-1">
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={mutation.isPending || draft.length >= 32}
              onClick={() => setDraft([...draft, ["", ""]])}
            >
              {t("Add label")}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={mutation.isPending || !draft.length}
              onClick={() => setDraft([])}
            >
              {t("Clear labels")}
            </Button>
          </div>
          {validation && (
            <p className="text-xs text-destructive">{t(validation)}</p>
          )}
          <div className="flex gap-1">
            <Button
              type="button"
              size="sm"
              disabled={mutation.isPending || query.isFetching || !!validation}
              onClick={() => mutation.mutate()}
            >
              {t("Save labels")}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={mutation.isPending}
              onClick={() => {
                setDraft(null);
                mutation.reset();
              }}
            >
              {t("Cancel")}
            </Button>
          </div>
        </div>
      )}
      <ErrorNotice
        error={mutation.error ?? query.error}
        retry={() => void refresh()}
      />
    </section>
  );
}

export function LabelPreview({ labels }: { labels: Labels }) {
  const { t } = useTranslation();
  return Object.keys(labels).length ? (
    <div className="flex max-h-48 flex-wrap gap-1 overflow-y-auto">
      {Object.entries(labels).map(([key, value]) => (
        <span
          className="max-w-full truncate rounded bg-muted px-1.5 py-0.5 text-xs"
          title={`${key}=${value}`}
          key={key}
        >
          {key}={value}
        </span>
      ))}
    </div>
  ) : (
    <span className="text-xs text-muted-foreground">{t("No labels")}</span>
  );
}

function validateRows(rows: LabelRow[]) {
  const keys = rows.map(([key]) => key);
  if (keys.some((key) => !validLabelKey(key)))
    return "Enter a valid label key.";
  if (new Set(keys).size !== keys.length) return "Label keys must be unique.";
  if (rows.some(([, value]) => !validLabelValue(value)))
    return "Enter a valid label value.";
  return undefined;
}
