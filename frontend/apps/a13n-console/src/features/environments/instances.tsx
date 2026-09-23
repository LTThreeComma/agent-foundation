import { MonitorIcon, PlusIcon } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, FormField, Input, ModalFrame, SearchPicker } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, data, type Schema } from "../../shared/api";
import {
  CollectionFooter,
  Empty,
  Pagination,
  ResourceIdentity,
  ResourceTable,
  useCursor,
} from "../../shared/collection";
import {
  ErrorNotice,
  InlineLoading,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import { FormActions } from "../../shared/forms";
import { ProviderIcon } from "../../shared/identity";
import { PageActions } from "../../shared/page";
import styles from "../../shared/shared.module.css";
import {
  createManagedEnvironment,
  environmentApi,
  environmentTemplates,
} from "./api";
import instanceStyles from "./environments.module.css";
import { EnvironmentPanel } from "./instance-details";
import { useEnvironmentTypes } from "./providers";

/** The environments that exist right now, with their lifecycle state. */
export function EnvironmentInstances() {
  const client = useClient(),
    { workspace, organization, can } = useWorkspace(),
    { t } = useTranslation(),
    page = useCursor(),
    [selected, setSelected] = useState<Schema["EnvironmentView"]>();
  const scope = { kind: "workspace", id: workspace.id } as const;
  const query = useQuery({
    queryKey: ["environments", workspace.id, page.cursor],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace_id}/environments", {
          params: {
            path: { workspace_id: workspace.id },
            query: { cursor: page.cursor },
          },
          signal,
        })
        .then(data),
    refetchInterval: 15_000,
  });
  const providers = useQuery({
    queryKey: ["environment-provider-options", "workspace", workspace.id],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        environmentApi(client, organization.id, scope).providers(
          signal,
          cursor,
        ),
      ),
    enabled: can("read"),
  });
  const templates = useQuery({
    queryKey: ["environment-template-options", workspace.id],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        environmentTemplates(client, workspace.id, signal, cursor),
      ),
    enabled: can("read"),
  });
  const providerById = new Map(
    providers.data?.map((provider) => [provider.id, provider]),
  );
  const templateById = new Map(
    templates.data?.map((template) => [template.id, template]),
  );
  function templateName(item: Schema["EnvironmentView"]) {
    if (!item.template_id) return t("External target");
    const template = templateById.get(item.template_id);
    if (template) return template.name;
    return templates.isPending ? <InlineLoading width="6rem" /> : t("Managed");
  }
  return (
    <div className={styles.stack}>
      <PageActions>{can("write") && <CreateEnvironment />}</PageActions>
      <ErrorNotice error={query.error} />
      {query.isPending ? (
        <Loading variant="table" columns={5} />
      ) : query.data?.items.length ? (
        <>
          <ResourceTable
            caption={t("Environment instances")}
            items={query.data.items}
            onRowActivate={(item) => setSelected(item)}
            columns={[
              {
                label: t("Environment"),
                tone: "primary",
                render: (item) => (
                  <ResourceIdentity
                    name={item.name}
                    resourceId={item.id}
                    icon={
                      <MonitorIcon
                        aria-hidden="true"
                        className="size-4 text-muted-foreground"
                      />
                    }
                    description={templateName(item)}
                  />
                ),
              },
              {
                label: t("Provider"),
                render: (item) => {
                  const provider = providerById.get(item.provider_id);
                  if (!provider)
                    return providers.isPending &&
                      providers.fetchStatus !== "idle" ? (
                      <InlineLoading width="5rem" />
                    ) : (
                      <span className={styles.muted}>{t("Not available")}</span>
                    );
                  return (
                    <span className={instanceStyles.providerCell}>
                      <ProviderIcon type={provider.type} />
                      <span>{provider.name}</span>
                    </span>
                  );
                },
              },
              {
                label: t("Status"),
                render: (item) => <StatePill state={item.status} />,
              },
              {
                label: t("Activity"),
                render: (item) => <Timestamp value={item.last_used_at} />,
              },
              {
                label: t("Updated"),
                tone: "muted",
                render: (item) => <Timestamp value={item.updated_at} />,
              },
            ]}
          />
          <CollectionFooter
            count={t("{{count}} environments on this page", {
              count: query.data.items.length,
            })}
          >
            <Pagination page={page} next={query.data.next_cursor} />
          </CollectionFooter>
        </>
      ) : (
        !query.error && (
          <Empty
            icon={<MonitorIcon aria-hidden="true" />}
            title={t("No environments yet")}
            description={t(
              "Choose an environment template when starting a conversation, or register an external environment.",
            )}
          />
        )
      )}
      {selected && (
        <EnvironmentPanel
          key={selected.id}
          environment={selected}
          open
          onClose={() => setSelected(undefined)}
        />
      )}
    </div>
  );
}

function CreateEnvironment() {
  const { t } = useTranslation(),
    [open, setOpen] = useState(false);
  return (
    <ModalFrame
      onOpenChange={setOpen}
      trigger={
        <Button variant="outline" type="button">
          <PlusIcon aria-hidden="true" />
          {t("Create environment")}
        </Button>
      }
      size={"md"}
      placement="top"
      title={t("Create environment")}
      description={t(
        "Allocate from a template or connect an externally managed target.",
      )}
      closeLabel={t("Close")}
      open={open}
    >
      {open && <EnvironmentForm close={() => setOpen(false)} />}
    </ModalFrame>
  );
}

function EnvironmentForm({ close }: { close: () => void }) {
  const client = useClient(),
    { workspace, organization } = useWorkspace(),
    cache = useQueryClient(),
    { t } = useTranslation();
  const scope = { kind: "workspace", id: workspace.id } as const;
  const templates = useQuery({
    queryKey: ["environment-template-options", workspace.id],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        environmentTemplates(client, workspace.id, signal, cursor),
      ),
  });
  const providers = useQuery({
    queryKey: ["environment-provider-options", "workspace", workspace.id],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        environmentApi(client, organization.id, scope).providers(
          signal,
          cursor,
        ),
      ),
  });
  const types = useEnvironmentTypes();
  const [kind, setKind] = useState("managed"),
    [name, setName] = useState(""),
    [templateId, setTemplateId] = useState(""),
    [providerId, setProviderId] = useState(""),
    [deviceId, setDeviceId] = useState("");
  const provider = providers.data?.find((item) => item.id === providerId);
  const save = useMutation({
    mutationFn: () => {
      const named = name.trim() ? { name: name.trim() } : {};
      if (kind === "managed") {
        if (!templateId) throw new Error(t("Select an environment template."));
        return createManagedEnvironment(client, workspace.id, {
          template_id: templateId,
          ...named,
        });
      }
      if (!provider) throw new Error(t("Select an environment provider."));
      const body: Schema["DeviceRegistration"] = {
        provider_id: providerId,
        device_id: deviceId.trim(),
        ...named,
      };
      return client.http
        .POST("/api/v1/workspaces/{workspace_id}/environments", {
          params: { path: { workspace_id: workspace.id } },
          body,
        })
        .then(data);
    },
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: ["environments"] });
      close();
    },
  });
  const templateOptions =
    templates.data
      ?.filter((item) => item.enabled)
      .map((item) => ({
        value: item.id,
        label: item.name,
        description: item.description ?? undefined,
        badge: t("Version {{version}}", { version: item.version }),
      })) ?? [];
  // An external target is a registered device of a connect-only provider.
  const providerOptions =
    providers.data
      ?.filter(
        (item) =>
          item.enabled &&
          types.data?.items.some(
            (definition) =>
              definition.type === item.type &&
              definition.supports_managed === false,
          ),
      )
      .map((item) => ({
        value: item.id,
        label: item.name,
        icon: <ProviderIcon type={item.type} />,
        description:
          types.data?.items.find((definition) => definition.type === item.type)
            ?.display_name ?? undefined,
      })) ?? [];
  return (
    <form
      className={styles.form}
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <FormField
        label={t("Name")}
        description={t("Leave empty to generate a name.")}
      >
        <Input
          value={name}
          onChange={(event) => setName(event.target.value)}
          maxLength={128}
        />
      </FormField>
      <FormField label={t("Ownership")}>
        <SearchPicker
          label={t("Ownership")}
          placeholder={t("Select ownership")}
          emptyMessage={t("No options")}
          value={kind}
          onValueChange={setKind}
          groups={[
            {
              label: t("Ownership"),
              options: [
                {
                  value: "managed",
                  label: t("Managed from template"),
                  description: t(
                    "Service allocates and retires the target for you.",
                  ),
                },
                {
                  value: "external",
                  label: t("External target"),
                  description: t(
                    "Register a target you run and keep control of.",
                  ),
                },
              ],
            },
          ]}
        />
      </FormField>
      <ErrorNotice error={templates.error ?? providers.error ?? types.error} />
      {kind === "managed" ? (
        <FormField label={t("Template")}>
          <SearchPicker
            label={t("Template")}
            placeholder={t("Select template")}
            emptyMessage={t("No matching templates")}
            value={templateId}
            onValueChange={setTemplateId}
            groups={[{ label: t("Templates"), options: templateOptions }]}
          />
        </FormField>
      ) : (
        <>
          <FormField label={t("Provider")}>
            <SearchPicker
              label={t("Provider")}
              placeholder={t("Select provider")}
              emptyMessage={t("No matching providers")}
              value={providerId}
              onValueChange={setProviderId}
              groups={[{ label: t("Providers"), options: providerOptions }]}
            />
          </FormField>
          <FormField
            label={t("Device ID")}
            description={t(
              "Use the device_id configured in envd. The provider owns the connection; each Run chooses its directory.",
            )}
          >
            <Input
              required
              value={deviceId}
              onChange={(event) => setDeviceId(event.target.value)}
            />
          </FormField>
        </>
      )}
      <ErrorNotice error={save.error} />
      <FormActions
        pending={save.isPending}
        onCancel={close}
        label={t("Create environment")}
      />
    </form>
  );
}
