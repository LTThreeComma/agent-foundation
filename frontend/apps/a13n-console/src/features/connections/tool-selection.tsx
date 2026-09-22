import { useTranslation } from "react-i18next";
import type { components } from "../../service-client";
import styles from "./connections.module.css";

export function ToolSelection({
  tools,
  safe,
  discovered,
  setTools,
  setSafe,
  disabled,
}: {
  tools: string[] | null;
  safe: string[];
  discovered?: components["schemas"]["ToolInfo"][];
  setTools: (tools: string[]) => void;
  setSafe: (tools: string[]) => void;
  disabled: boolean;
}) {
  const { t } = useTranslation();
  return (
    <fieldset className={styles.tools}>
      <legend>{t("Available tools")}</legend>
      <p className={styles.help}>
        {t(
          "Allow tools here; agents choose their own subset. Retry-safe is your assertion that repeating an interrupted call is acceptable.",
        )}
      </p>
      {(
        discovered ??
        tools?.map((name) => ({
          name,
          description: null,
          input_schema: {},
        })) ??
        []
      ).map((tool) => (
        <div key={tool.name} className={styles.tool}>
          <label>
            <input
              type="checkbox"
              checked={tools?.includes(tool.name) ?? true}
              disabled={disabled}
              onChange={(event) => {
                const selected = tools ?? [];
                setTools(
                  event.target.checked
                    ? [...selected, tool.name]
                    : selected.filter((name) => name !== tool.name),
                );
                if (!event.target.checked)
                  setSafe(safe.filter((name) => name !== tool.name));
              }}
            />{" "}
            {tool.name}
          </label>
          {tool.description && (
            <p className={styles.help}>{tool.description}</p>
          )}
          <label className={styles.help}>
            <input
              type="checkbox"
              checked={safe.includes(tool.name)}
              disabled={disabled || !tools?.includes(tool.name)}
              onChange={(event) =>
                setSafe(
                  event.target.checked
                    ? [...safe, tool.name]
                    : safe.filter((name) => name !== tool.name),
                )
              }
            />{" "}
            {t("Retry-safe after interruption")}
          </label>
        </div>
      ))}
    </fieldset>
  );
}
