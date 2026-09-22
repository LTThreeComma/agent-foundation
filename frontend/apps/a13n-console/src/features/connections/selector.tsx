import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button, ChoiceField } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useScope } from "../../layout/workspace";
import { data, type components } from "../../service-client";
import { ErrorNotice } from "../../shared/feedback";
import { Pagination, useCursor } from "../../shared/collection";
import styles from "./connections.module.css";

type Selection = components["schemas"]["ConnectionSelection"];
export function ConnectionSelector({
  value,
  onChange,
  disabled,
}: {
  value: Selection[];
  onChange: (value: Selection[]) => void;
  disabled: boolean;
}) {
  const { client, path, cache, base } = useScope();
  const { t } = useTranslation();
  const page = useCursor();
  const [id, setId] = useState("");
  const [discovered, setDiscovered] =
    useState<components["schemas"]["ConnectionTest"]>();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const query = useQuery({
    queryKey: [...cache, "connections", page.cursor],
    queryFn: async ({ signal }) =>
      data(
        await client.http.GET("/api/v1/workspaces/{workspace_id}/connections", {
          params: { path, query: { limit: 50, cursor: page.cursor } },
          signal,
        }),
      ),
  });
  const selected = query.data?.items.find((item) => item.id === id);
  const selectedTools =
    value.find((item) => item.connection_id === id)?.tools ?? [];
  const update = (connection_id: string, tools: string[]) =>
    onChange([
      ...value.filter((item) => item.connection_id !== connection_id),
      ...(tools.length ? [{ connection_id, tools }] : []),
    ]);
  return (
    <div className={styles.form}>
      <p className={styles.help}>
        {t(
          "Choose only the tools this agent needs. Saving keeps the selection in this revision; endpoint and credentials stay current.",
        )}{" "}
        <Link to={`${base}/connections`}>{t("Manage connections")}</Link>
      </p>
      {value.map((item) => (
        <div className={styles.row} key={item.connection_id}>
          <span>
            {query.data?.items.find(
              (connection) => connection.id === item.connection_id,
            )?.name ?? item.connection_id}
            : {item.tools.join(", ")}
          </span>
          <Button
            type="button"
            variant="ghost"
            disabled={disabled}
            onClick={() => update(item.connection_id, [])}
          >
            {t("Remove")}
          </Button>
        </div>
      ))}
      <ChoiceField
        label={t("Connection")}
        value={id}
        disabled={disabled}
        options={(query.data?.items ?? [])
          .filter((item) => item.enabled)
          .map((item) => ({ value: item.id, label: item.name }))}
        onValueChange={(id) => {
          setId(id);
          setDiscovered(undefined);
          setError(null);
        }}
      />
      <Pagination page={page} next={query.data?.next_cursor} />
      <Button
        type="button"
        variant="outline"
        disabled={disabled || !id || pending}
        onClick={async () => {
          setPending(true);
          setError(null);
          try {
            setDiscovered(
              data(
                await client.http.GET(
                  "/api/v1/workspaces/{workspace_id}/connections/{connection_id}/tools",
                  { params: { path: { ...path, connection_id: id } } },
                ),
              ),
            );
          } catch (failure) {
            setError(failure);
          } finally {
            setPending(false);
          }
        }}
      >
        {t(pending ? "Loading tools…" : "Choose tools")}
      </Button>
      {discovered?.connection_id === id && (
        <div className={styles.tools}>
          {discovered.tools
            .filter(
              (tool) =>
                !selected?.config.tools ||
                selected.config.tools.includes(tool.name),
            )
            .map((tool) => (
              <div className={styles.tool} key={tool.name}>
                <label>
                  <input
                    type="checkbox"
                    checked={selectedTools.includes(tool.name)}
                    disabled={disabled}
                    onChange={(event) =>
                      update(
                        id,
                        event.target.checked
                          ? [...selectedTools, tool.name]
                          : selectedTools.filter((name) => name !== tool.name),
                      )
                    }
                  />{" "}
                  {tool.name}
                </label>
                {tool.description && (
                  <p className={styles.help}>{tool.description}</p>
                )}
              </div>
            ))}
        </div>
      )}
      <ErrorNotice error={error ?? query.error} />
    </div>
  );
}
