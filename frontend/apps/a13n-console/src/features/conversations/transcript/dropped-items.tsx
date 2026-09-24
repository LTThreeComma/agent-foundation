import { useTranslation } from "react-i18next";
import styles from "./transcript.module.css";

/** A run's display keeps its newest Items; the earliest it dropped cannot be read. */
export function DroppedItems({ count }: { count: number }) {
  const { t } = useTranslation();
  if (!count) return null;
  return (
    <p role="status" className={styles.notice}>
      {t("{{count}} earlier items not shown", { count })}
    </p>
  );
}
