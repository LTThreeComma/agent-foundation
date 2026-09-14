import { Popover, PopoverPopup, PopoverTrigger } from "a13n-ui";
import type { ReactNode } from "react";
import { LabelPreview } from "./resource-labels";
import { HashStraightIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { CopyButton } from "./copy";
import styles from "./resource-reference.module.css";

export function ResourceReference({
  id,
  resourceKey,
  labels,
  children,
}: {
  id: string;
  resourceKey?: string;
  labels?: Record<string, string>;
  children?: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <Popover>
      <PopoverTrigger
        type="button"
        className={styles.marker}
        aria-label={t("Show resource reference")}
        openOnHover
        delay={100}
        closeDelay={120}
      >
        <HashStraightIcon aria-hidden="true" />
      </PopoverTrigger>
      <PopoverPopup
        className={styles.popup}
        align="start"
        sideOffset={5}
        tooltipStyle
      >
        <ReferenceRow
          label={t("ID")}
          value={id}
          copyLabel={t("Copy resource ID")}
        />
        {resourceKey && (
          <ReferenceRow
            label={t("Key")}
            value={resourceKey}
            copyLabel={t("Copy resource key")}
          />
        )}
        {children ??
          (labels !== undefined && (
            <section className="border-t border-border pt-2 mt-1">
              <div className="mb-2 text-xs text-muted-foreground">
                {t("Labels")}
              </div>
              <LabelPreview labels={labels} />
            </section>
          ))}
      </PopoverPopup>
    </Popover>
  );
}

function ReferenceRow({
  label,
  value,
  copyLabel,
}: {
  label: string;
  value: string;
  copyLabel: string;
}) {
  return (
    <div className={styles.row}>
      <span className={styles.label}>{label}</span>
      <code title={value}>{value}</code>
      <CopyButton value={value} iconOnly copyLabel={copyLabel} />
    </div>
  );
}
