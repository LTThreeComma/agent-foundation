import {
  Button,
  DisclosureSection,
  FormField,
  Input,
  ModalFrame,
  Tabs,
  TabsList,
  TabsPanel,
  TabsTab,
} from "a13n-ui";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import {
  useResourceEditorState,
  type ResourceEditorControl,
} from "../../shared/resource-modal";
import { ErrorNotice, Loading, StateBadge } from "../../shared/feedback";
import { Confirm, FormActions, JsonView } from "../../shared/form";
import { useIdempotency } from "../../shared/idempotency";
import styles from "../../shared/shared.module.css";
import { ConnectionSetup } from "./setup";

export function ConnectionDetails({
  connectionId,
  onCleanup,
  controlledOpen,
  onClose,
  finalFocus,
}: ResourceEditorControl & {
  connectionId: string;
  onCleanup: (receipt: Schema["ConnectionCleanupReceipt"]) => void;
}) {
  const client = useClient(),
    { can, workspace } = useWorkspace(),
    { t } = useTranslation(),
    [generation, setGeneration] = useState(0);
  const { open, setOpen, modalProps } = useResourceEditorState({
    controlledOpen,
    onClose,
    finalFocus,
  });
  const query = useQuery({
    queryKey: ["connector-connections", workspace.id, connectionId],
    enabled: open,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/connector-connections/{connection_id}", {
          params: { path: { connection_id: connectionId } },
          signal,
        })
        .then(data)
        .then((connection) => {
          if (connection.workspace_id !== workspace.id)
            throw new Error(t("Connection belongs to another workspace."));
          return connection;
        }),
  });
  async function reload() {
    await query.refetch();
    setGeneration((value) => value + 1);
  }
  return (
    <ModalFrame
      {...modalProps}
      trigger={
        controlledOpen === undefined ? (
          <Button size="sm" variant="outline" type="button">
            {t("Details")}
          </Button>
        ) : undefined
      }
      size={"lg"}
      title={query.data?.name ?? t("Connection")}
      description={t(
        "Manage this workspace connection and its external authorization.",
      )}
      closeLabel={t("Close")}
    >
      {open &&
        (query.isPending ? (
          <Loading />
        ) : query.error ? (
          <ErrorNotice error={query.error} />
        ) : (
          query.data &&
          (can("connector_connection.manage") ? (
            <Tabs
              key={generation}
              defaultValue={
                ["pending", "action_required"].includes(query.data.status)
                  ? "setup"
                  : "details"
              }
            >
              <TabsList aria-label={t("Connection details")}>
                <TabsTab value={"details"}>{t("Details")}</TabsTab>
                <TabsTab value={"setup"}>{t("Authorization")}</TabsTab>
              </TabsList>
              <TabsPanel value={"details"}>
                {
                  <ConnectionSettings
                    onCleanup={onCleanup}
                    initial={query.data}
                    close={() => setOpen(false)}
                    reload={reload}
                  />
                }
              </TabsPanel>
              <TabsPanel value={"setup"}>
                {<ConnectionSetup connection={query.data} />}
              </TabsPanel>
            </Tabs>
          ) : (
            <JsonView value={query.data} />
          ))
        ))}
    </ModalFrame>
  );
}
function ConnectionSettings({
  initial,
  close,
  reload,
  onCleanup,
}: {
  onCleanup: (receipt: Schema["ConnectionCleanupReceipt"]) => void;
  initial: Schema["ConnectorConnection"];
  close: () => void;
  reload: () => Promise<void>;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation(),
    key = useIdempotency(),
    [basis] = useState(initial),
    [name, setName] = useState(initial.name);
  function done() {
    void cache.invalidateQueries({ queryKey: ["connector-connections"] });
    close();
  }
  const save = useMutation({
    mutationFn: () =>
      client.http
        .PATCH("/api/v1/connector-connections/{connection_id}", {
          params: { path: { connection_id: basis.id } },
          body: { name, expected_version: basis.version },
        })
        .then(data),
    onSuccess: done,
  });
  const body = { expected_version: basis.version };
  return (
    <div className={styles.stack}>
      <StateBadge state={basis.status} />
      <form
        className={styles.form}
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        <FormField className="min-w-0 w-full" label={t("Name")}>
          <Input
            required={true}
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={128}
          />
        </FormField>
        <ErrorNotice error={save.error} retry={() => void reload()} />
        <FormActions pending={save.isPending} />
      </form>
      <DisclosureSection title={<>{t("Account metadata")}</>}>
        <JsonView value={basis.safe_metadata} />
      </DisclosureSection>
      <div className={styles.actions}>
        <Confirm
          title={t(
            basis.status === "disabled"
              ? "Enable connection"
              : "Disable connection",
          )}
          description={t(
            "This changes whether new agent calls can use the connection.",
          )}
          trigger={t(basis.status === "disabled" ? "Enable" : "Disable")}
          action={async () => {
            const action = basis.status === "disabled" ? "enable" : "disable";
            await client.http.POST(
              "/api/v1/connector-connections/{connection_id}/{action}",
              {
                params: {
                  path: { connection_id: basis.id, action },
                  header: commandHeaders(
                    workspace.id,
                    key.forBody({ action, ...body }),
                  ),
                },
                body,
              },
            );
            done();
          }}
        />
        {(["revoke", "delete"] as const).map((action) => (
          <Confirm
            key={action}
            title={t(
              action === "revoke"
                ? "Revoke authorization"
                : "Delete connection",
            )}
            description={t(
              "Local access is disabled immediately. The result reports whether external cleanup succeeded.",
            )}
            trigger={t(action === "revoke" ? "Revoke" : "Delete")}
            danger
            action={async () => {
              const header = commandHeaders(
                workspace.id,
                key.forBody({ action, ...body }),
              );
              const result =
                action === "revoke"
                  ? data(
                      await client.http.POST(
                        "/api/v1/connector-connections/{connection_id}/revoke",
                        {
                          params: { path: { connection_id: basis.id }, header },
                          body,
                        },
                      ),
                    )
                  : data(
                      await client.http.DELETE(
                        "/api/v1/connector-connections/{connection_id}",
                        {
                          params: {
                            path: { connection_id: basis.id },
                            header,
                            query: body,
                          },
                        },
                      ),
                    );
              onCleanup(result);
              done();
            }}
          />
        ))}
      </div>
    </div>
  );
}
