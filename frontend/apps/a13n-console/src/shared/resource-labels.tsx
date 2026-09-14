import { Button, FormField, Input, ModalFrame } from "a13n-ui";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ComponentProps } from "react";
import { useTranslation } from "react-i18next";
import { ErrorNotice } from "./feedback";

type Labels = Record<string, string>;
type LabelRow = [string, string];
type LabelsRepresentation = { value: { labels: Labels }; etag?: string };

export function ResourceLabelsDialog({
  resourceId,
  labels,
  editable,
  read,
  write,
  onSaved,
  label,
}: {
  resourceId: string;
  labels?: Labels;
  editable: boolean;
  read: (signal: AbortSignal) => Promise<LabelsRepresentation>;
  write: (labels: Labels, etag: string) => Promise<unknown>;
  onSaved?: () => void;
  label?: string;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [baseline, setBaseline] = useState<LabelsRepresentation>();
  const [reset, setReset] = useState(0);
  const query = useQuery({
    queryKey: ["resource-labels", resourceId],
    queryFn: ({ signal }) => read(signal),
    enabled: false,
  });
  const load = async () => {
    const result = await query.refetch();
    if (result.isSuccess) {
      setBaseline(result.data);
      setReset((value) => value + 1);
    }
  };
  const mutation = useMutation({
    mutationFn: (next: Labels) => {
      if (!baseline?.etag)
        throw new Error(
          t("Version information is unavailable. Reload this page."),
        );
      return write(next, baseline.etag);
    },
    onSuccess: async () => {
      await load();
      onSaved?.();
    },
  });
  const current = baseline?.value.labels ?? labels ?? {};
  const countKnown = !!baseline || labels !== undefined;
  const displayLabel = label ?? t("Labels");
  return (
    <ModalFrame
      open={open}
      onOpenChange={(value) => {
        setOpen(value);
        if (value) {
          mutation.reset();
          setBaseline(undefined);
          void load();
        }
      }}
      trigger={
        <Button variant="outline" size="sm" type="button">
          {displayLabel}{" "}
          {countKnown && (
            <span className="text-muted-foreground">
              {Object.keys(current).length}
            </span>
          )}
        </Button>
      }
      title={label ?? t("Resource labels")}
      description={t("Classify this resource with exact key-value labels.")}
      closeLabel={t("Close")}
    >
      {!baseline && query.isFetching ? (
        <p className="text-sm text-muted-foreground">{t("Loading labels…")}</p>
      ) : !baseline && query.error ? (
        <ErrorNotice error={query.error} retry={() => void load()} />
      ) : baseline ? (
        <ResourceLabels
          labels={baseline.value.labels}
          resetKey={reset}
          editable={editable}
          pending={mutation.isPending}
          error={mutation.error}
          save={(next) => mutation.mutate(next)}
          refresh={() => {
            mutation.reset();
            void load();
          }}
        />
      ) : null}
    </ModalFrame>
  );
}

export function ResourceLabels({
  labels,
  editable,
  pending,
  error,
  save,
  refresh,
  resetKey,
}: {
  labels: Labels;
  editable: boolean;
  pending?: boolean;
  error?: unknown;
  save: (labels: Labels) => void;
  refresh: () => void;
  resetKey?: unknown;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<LabelRow[]>(Object.entries(labels));
  useEffect(() => setDraft(Object.entries(labels)), [resetKey]);
  const validation = validateRows(draft);
  return (
    <section className="space-y-3" aria-label={t("Labels")}>
      <div className="flex items-center justify-between gap-3">
        <h3 className="font-medium">{t("Labels")}</h3>
        {editable && draft.length < 32 && (
          <Button
            variant="ghost"
            type="button"
            disabled={pending}
            onClick={() => setDraft((value) => [...value, ["", ""]])}
          >
            {t("Add label")}
          </Button>
        )}
      </div>
      {!editable && <LabelPreview labels={labels} />}
      {editable && draft.length > 0 && (
        <LabelRowsEditor rows={draft} onChange={setDraft} disabled={pending} />
      )}
      {editable && draft.length === 0 && (
        <p className="text-sm text-muted-foreground">{t("No labels")}</p>
      )}
      {validation && (
        <p className="text-sm text-destructive">{t(validation)}</p>
      )}
      <ErrorNotice error={error} />
      {editable && (
        <div className="flex gap-2">
          <Button
            type="button"
            disabled={pending || !!validation}
            onClick={() => save(Object.fromEntries(draft))}
          >
            {t("Save labels")}
          </Button>
          <Button
            variant="outline"
            type="button"
            disabled={pending || draft.length === 0}
            onClick={() => setDraft([])}
          >
            {t("Clear labels")}
          </Button>
          <Button
            variant="ghost"
            type="button"
            disabled={pending}
            onClick={refresh}
          >
            {t("Refresh labels")}
          </Button>
        </div>
      )}
    </section>
  );
}

export function LabelOverridesField({
  value,
  inherited = {},
  onChange,
  onValidityChange,
  provisional = false,
  title,
}: {
  value: Labels;
  inherited?: Labels;
  onChange: (labels: Labels) => void;
  onValidityChange?: (valid: boolean) => void;
  provisional?: boolean;
  title?: string;
}) {
  const { t } = useTranslation();
  const [rows, setRows] = useState<LabelRow[]>(Object.entries(value));
  const validation = validateRows(rows, inherited);
  const merged = { ...inherited, ...Object.fromEntries(rows) };
  useEffect(() => onValidityChange?.(!validation), [validation]);
  const update = (next: LabelRow[]) => {
    setRows(next);
    if (!validateRows(next, inherited)) onChange(Object.fromEntries(next));
  };
  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-medium">
            {title ?? t("Label overrides")}
          </h3>
          <p className="text-sm text-muted-foreground">
            {t(
              provisional
                ? "Preview uses current parent labels; queued acceptance may use newer labels."
                : "Inherited labels are copied once when the resource is created.",
            )}
          </p>
        </div>
        {rows.length < 32 && (
          <Button
            variant="outline"
            size="sm"
            type="button"
            onClick={() => update([...rows, ["", ""]])}
          >
            {t("Add label")}
          </Button>
        )}
      </div>
      {rows.length > 0 && (
        <LabelRowsEditor rows={rows} onChange={update} inherited={inherited} />
      )}
      {validation && (
        <p className="text-sm text-destructive">{t(validation)}</p>
      )}
      <div>
        <p className="mb-2 text-xs font-medium text-muted-foreground">
          {t("Created resource preview")}
        </p>
        <LabelPreview labels={merged} />
      </div>
    </section>
  );
}

function LabelRowsEditor({
  rows,
  onChange,
  inherited = {},
  disabled,
}: {
  rows: LabelRow[];
  onChange: (rows: LabelRow[]) => void;
  inherited?: Labels;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const keys = rows.map(([key]) => key);
  const mergedCount = Object.keys({
    ...inherited,
    ...Object.fromEntries(rows),
  }).length;
  const replace = (index: number, row: LabelRow) =>
    onChange(
      rows.map((current, position) => (position === index ? row : current)),
    );
  return (
    <div className="space-y-2">
      {rows.map(([key, value], index) => {
        const keyError = !validKey(key)
          ? "Enter a valid label key."
          : keys.indexOf(key) !== keys.lastIndexOf(key)
            ? "Label keys must be unique."
            : mergedCount > 32
              ? "Labels may contain at most 32 entries."
              : undefined;
        const valueError = !validValue(value)
          ? "Enter a valid label value."
          : undefined;
        return (
          <div
            className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] gap-2"
            key={index}
          >
            <FormField label={t("Label key")} hideLabel>
              <ValidatedInput
                disabled={disabled}
                maxLength={63}
                required
                validationMessage={keyError ? t(keyError) : undefined}
                value={key}
                onChange={(next) => replace(index, [next, value])}
              />
            </FormField>
            <FormField label={t("Label value")} hideLabel>
              <ValidatedInput
                disabled={disabled}
                validationMessage={valueError ? t(valueError) : undefined}
                value={value}
                onChange={(next) => replace(index, [key, next])}
              />
            </FormField>
            <Button
              variant="ghost"
              type="button"
              disabled={disabled}
              onClick={() =>
                onChange(rows.filter((_, position) => position !== index))
              }
            >
              {t("Remove")}
            </Button>
          </div>
        );
      })}
    </div>
  );
}

function ValidatedInput({
  value,
  onChange,
  validationMessage,
  ...props
}: Omit<ComponentProps<typeof Input>, "onChange"> & {
  onChange: (value: string) => void;
  validationMessage?: string;
}) {
  const input = useRef<HTMLInputElement>(null);
  useEffect(
    () => input.current?.setCustomValidity(validationMessage ?? ""),
    [validationMessage],
  );
  return (
    <Input
      {...props}
      ref={input}
      aria-invalid={!!validationMessage}
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}

function LabelPreview({ labels }: { labels: Labels }) {
  const { t } = useTranslation();
  return Object.keys(labels).length > 0 ? (
    <div className="flex flex-wrap gap-2">
      {Object.entries(labels).map(([key, value]) => (
        <span className="rounded-md bg-muted px-2 py-1 text-xs" key={key}>
          <span className="font-medium">{key}</span>=<span>{value}</span>
        </span>
      ))}
    </div>
  ) : (
    <span className="text-sm text-muted-foreground">{t("No labels")}</span>
  );
}

function validateRows(rows: LabelRow[], inherited: Labels = {}) {
  const keys = rows.map(([key]) => key);
  if (keys.some((key) => !validKey(key))) return "Enter a valid label key.";
  if (new Set(keys).size !== keys.length) return "Label keys must be unique.";
  if (rows.some(([, value]) => !validValue(value)))
    return "Enter a valid label value.";
  if (Object.keys({ ...inherited, ...Object.fromEntries(rows) }).length > 32)
    return "Labels may contain at most 32 entries.";
  return undefined;
}

const validKey = (key: string) => /^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$/.test(key);
const validValue = (value: string) =>
  Array.from(value).length <= 256 && !/[\p{Cc}\p{Cs}]/u.test(value);
