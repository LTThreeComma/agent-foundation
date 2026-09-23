import { SlidersHorizontalIcon } from "@phosphor-icons/react";
import { Input } from "a13n-ui";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import {
  DirectoryEmpty,
  DirectoryGroup,
  DirectoryList,
  DirectoryRow,
} from "../../shared/dialogs";
import { ModelIcon } from "./model-icon";
import styles from "./models.module.css";

type Entry = Schema["CatalogModel"];
const MAX_VISIBLE_MODELS = 100;
/** A catalogue key names the channel that serves the model, then its upstream name. */
export const catalogRef = (entry: Entry) => ({
  provider: entry.key.split(":")[0],
  model: entry.model_name,
});

/**
 * The model step of the add flow: search the models the provider's catalogue
 * lists and pick one, or add one it does not list.
 */
export function CatalogPicker({
  entries,
  providerName,
  value,
  onSelect,
}: {
  entries: readonly Entry[];
  providerName?: string;
  /** The catalogue key the draft was seeded from. */
  value: string | null;
  /** `null` chooses a custom model with no catalog identity. */
  onSelect: (entry: Entry | null) => void;
}) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const search = query.trim().toLocaleLowerCase();
  const matches = useMemo(
    () =>
      [...entries]
        .sort((a, b) => a.model_name.localeCompare(b.model_name))
        .filter(
          (entry) => !search || entry.key.toLocaleLowerCase().includes(search),
        ),
    [entries, search],
  );
  const visible = matches.slice(0, MAX_VISIBLE_MODELS);
  return (
    <div className={styles.step}>
      <DirectoryList
        search={
          <Input
            type="search"
            autoFocus
            aria-label={t("Search models…")}
            placeholder={t("Search models…")}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        }
        footer={
          <div className={styles.catalogFooter}>
            <span>
              {matches.length > MAX_VISIBLE_MODELS
                ? t("Showing first 100 models. Search to narrow the list.")
                : ""}
            </span>
          </div>
        }
      >
        {visible.length > 0 && (
          <DirectoryGroup
            label={
              providerName
                ? t("Models from {{provider}}", { provider: providerName })
                : t("Models")
            }
          >
            {visible.map((entry) => (
              <DirectoryRow
                key={entry.key}
                icon={
                  <ModelIcon
                    upstream={entry.model_name}
                    catalogRef={catalogRef(entry)}
                    size={20}
                  />
                }
                name={entry.model_name}
                detail={entry.key}
                meta={entry.key === value ? t("Selected") : undefined}
                onClick={() => onSelect(entry)}
              />
            ))}
          </DirectoryGroup>
        )}
        {!visible.length && (
          <DirectoryEmpty>
            {t("No models found. You can still add a model manually.")}
          </DirectoryEmpty>
        )}
        <DirectoryGroup label={t("Not listed")}>
          <DirectoryRow
            icon={<SlidersHorizontalIcon size={18} aria-hidden="true" />}
            name={t("Custom model")}
            detail={t("Enter an upstream model ID")}
            onClick={() => onSelect(null)}
          />
        </DirectoryGroup>
      </DirectoryList>
    </div>
  );
}
