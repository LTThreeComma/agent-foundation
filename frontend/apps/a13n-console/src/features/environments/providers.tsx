import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useAccess } from "../../layout/workspace";
import { data, ifMatch, representation, type Schema } from "../../shared/api";
import { useCursor } from "../../shared/collection";
import {
  CatalogStep,
  useResourceEditorState,
  useResourceRows,
  type ResourceEditorControl,
} from "../../shared/dialogs";
import { ErrorNotice } from "../../shared/feedback";
import { useCredentialSection } from "../../shared/use-credential-section";
import {
  FormActions,
  ProviderEnabled,
  ProviderKeyLink,
  SchemaFields,
  jsonObject,
  validateSettings,
} from "../../shared/forms";
import {
  AddProviderDialog,
  ConnectionTest,
  CredentialRow,
  EditProviderDialog,
  ProviderConnectFields,
  ProviderEditor,
  ProviderFacts,
  ProviderGroup,
  ProviderName,
  ProviderTable,
  credentialDescription,
  credentialHint,
  credentialLabel,
  providerKeyLink,
  providerStyles,
  providerTestResult,
} from "../providers";
import { environmentApi, type EnvironmentScope } from "./api";

type Definition = Schema["ProviderType"];

export function useEnvironmentTypes(enabled = true) {
  const client = useClient();
  return useQuery({
    queryKey: ["environment-types"],
    enabled,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/provider-types/{kind}", {
          params: { path: { kind: "environment" } },
          signal,
        })
        .then(data),
  });
}
function schema(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? Object.fromEntries(Object.entries(value))
    : {};
}

export function EnvironmentProviders({ scope }: { scope: EnvironmentScope }) {
  const providerTypes = useEnvironmentTypes();
  const client = useClient(),
    { can, organizationCan, organization } = useAccess(),
    { t } = useTranslation(),
    page = useCursor(),
    api = environmentApi(client, organization.id, scope);
  const rows = useResourceRows<Schema["Provider"]>();
  const query = useQuery({
    queryKey: [
      "environment-providers",
      scope.kind,
      scope.id,
      "list",
      page.cursor,
    ],
    queryFn: ({ signal }) => api.providers(signal, page.cursor),
  });
  const manage =
    scope.kind === "organization" ? organizationCan("write") : can("write");
  const connectable = providerTypes.data?.items ?? [];
  const add =
    manage && connectable.length ? (
      <AddEnvironmentProvider scope={scope} definitions={connectable} />
    ) : undefined;
  const definitionFor = (type: string) =>
    providerTypes.data?.items.find((entry) => entry.type === type);
  return (
    <>
      {rows.selected && (
        <EditEnvironmentProvider
          key={rows.selected.id}
          scope={
            rows.selected.workspace_id
              ? { kind: "workspace", id: rows.selected.workspace_id }
              : { kind: "organization", id: rows.selected.organization_id }
          }
          provider={rows.selected}
          {...rows.control}
        />
      )}
      <ProviderTable
        category="environments"
        items={query.data?.items}
        isPending={query.isPending}
        error={query.error}
        page={page}
        nextCursor={query.data?.next_cursor}
        action={add}
        canActivateRow={(item) =>
          item.workspace_id ? manage : organizationCan("write")
        }
        onRowActivate={rows.activate}
        notice={
          <p className={providerStyles.notice}>
            {t(
              "Direct Local and Docker require a single-host deployment and operator configuration.",
            )}
          </p>
        }
        row={(item) => ({
          id: item.id,
          name: item.name,
          type: item.type,
          definition: definitionFor(item.type)?.display_name ?? item.type,
          workspaceId: item.workspace_id,
          credentials:
            definitionFor(item.type)?.credential_schema == null
              ? ("not_required" as const)
              : item.credential_configured
                ? ("configured" as const)
                : ("not_configured" as const),
          state: item.enabled ? "enabled" : "disabled",
        })}
      />
    </>
  );
}

/** Catalog-first creation for the providers an operator can connect. */
function AddEnvironmentProvider({
  scope,
  definitions,
}: {
  scope: EnvironmentScope;
  definitions: Definition[];
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [generation, setGeneration] = useState(0);
  // Closing discards the step and the draft, whoever asked for it.
  function change(value: boolean) {
    setOpen(value);
    if (!value) setGeneration((current) => current + 1);
  }
  return (
    <AddProviderDialog<Definition>
      key={generation}
      definitions={definitions}
      open={open}
      onOpenChange={change}
      description={t("Choose where your environments run.")}
      hint={(definition) => credentialHint(definition.credential_schema)}
      connectDescription={(definition) =>
        t(credentialDescription(definition.credential_schema), {
          provider: definition.display_name,
        })
      }
    >
      {(definition, back) => (
        <CatalogStep backLabel={t("All providers")} onBack={back}>
          <ProviderForm
            scope={scope}
            definition={definition}
            definitions={definitions}
            close={() => change(false)}
            reload={async () => {}}
          />
        </CatalogStep>
      )}
    </AddProviderDialog>
  );
}

function EditEnvironmentProvider({
  scope,
  provider,
  controlledOpen,
  onClose,
  finalFocus,
}: ResourceEditorControl & {
  scope: EnvironmentScope;
  provider: Schema["Provider"];
}) {
  const client = useClient(),
    [generation, setGeneration] = useState(0),
    definitions = useEnvironmentTypes();
  const state = useResourceEditorState({ controlledOpen, onClose, finalFocus });
  const query = useQuery({
    queryKey: [
      "environment-providers",
      scope.kind,
      scope.id,
      "detail",
      provider.id,
    ],
    enabled: state.open,
    queryFn: ({ signal }) =>
      client.http
        .GET(
          "/api/v1/organizations/{organization_id}/environment-providers/{provider_id}",
          {
            params: {
              path: {
                organization_id: provider.organization_id,
                provider_id: provider.id,
              },
            },
            signal,
          },
        )
        .then(representation),
  });
  const definition = definitions.data?.items.find(
    (item) => item.type === provider.type,
  );
  return (
    <EditProviderDialog
      modalProps={state.modalProps}
      open={state.open}
      name={query.data?.value.name ?? provider.name}
      id={provider.id}
      type={provider.type}
      definition={definition?.display_name}
      scope={provider.workspace_id ? "workspace" : "organization"}
      loading={definitions.isPending || query.isPending}
      error={definitions.error ?? query.error}
    >
      {query.data && (
        <ProviderForm
          key={generation}
          scope={scope}
          initial={query.data}
          definitions={definitions.data?.items ?? []}
          close={() => state.setOpen(false)}
          reload={async () => {
            await query.refetch();
            setGeneration((value) => value + 1);
          }}
        />
      )}
    </EditProviderDialog>
  );
}

function ProviderForm({
  scope,
  initial,
  definition: chosen,
  definitions,
  close,
  reload,
}: {
  scope: EnvironmentScope;
  initial?: ReturnType<typeof representation<Schema["Provider"]>>;
  definition?: Definition;
  definitions: Definition[];
  close: () => void;
  reload: () => Promise<void>;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { t } = useTranslation(),
    { organization } = useAccess(),
    [basis] = useState(initial),
    [name, setName] = useState(
      initial?.value.name ?? chosen?.display_name ?? "",
    ),
    type = initial?.value.type ?? chosen?.type ?? "",
    [enabled, setEnabled] = useState(initial?.value.enabled ?? true),
    [configuration, setConfiguration] = useState<Record<string, unknown>>(
      initial?.value.config ?? {},
    ),
    [advancedOpen, setAdvancedOpen] = useState(false);
  const api = environmentApi(client, organization.id, scope),
    definition = definitions.find((item) => item.type === type) ?? chosen,
    configSchema = schema(definition?.configuration_schema),
    section = useCredentialSection(definition, configuration, basis?.value);
  function done() {
    void cache.invalidateQueries({ queryKey: ["environment-providers"] });
    close();
  }
  const save = useMutation({
    mutationFn: async () => {
      const credential = section.payload();
      if (credential) validateSettings(section.schema, credential);
      if (basis) {
        return client.http
          .PATCH(
            "/api/v1/organizations/{organization_id}/environment-providers/{provider_id}",
            {
              params: {
                path: {
                  organization_id: basis.value.organization_id,
                  provider_id: basis.value.id,
                },
              },
              headers: ifMatch(basis.etag),
              body: {
                name,
                enabled,
                ...(credential === undefined
                  ? {}
                  : {
                      credential:
                        credential === null
                          ? null
                          : jsonObject(JSON.stringify(credential)),
                    }),
              },
            },
          )
          .then(data);
      }
      validateSettings(configSchema, configuration);
      return api.createProvider({
        name,
        type,
        config: jsonObject(JSON.stringify(configuration)),
        ...(credential
          ? { credential: jsonObject(JSON.stringify(credential)) }
          : {}),
      });
    },
    onSuccess: done,
  });
  if (!basis)
    return (
      <form
        className={providerStyles.connectForm}
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        <ProviderConnectFields
          credentialSchema={
            section.mode === "forbidden" ? undefined : section.schema
          }
          configurationSchema={configSchema}
          credential={section.credential}
          onCredentialChange={section.setCredential}
          configuration={configuration}
          onConfigurationChange={setConfiguration}
          name={name}
          onNameChange={setName}
          keyLink={providerKeyLink(definition)}
          advancedOpen={advancedOpen}
          onAdvancedOpenChange={setAdvancedOpen}
        />
        <ErrorNotice error={save.error} />
        <FormActions
          pending={save.isPending}
          onCancel={close}
          label={t("Add provider")}
        />
      </form>
    );
  return (
    <ProviderEditor
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <ProviderName value={name} onChange={setName} />
      <ProviderGroup>
        <ProviderEnabled checked={enabled} onCheckedChange={setEnabled} />
        {section.visible && (
          <CredentialRow
            label={t(credentialLabel(section.schema))}
            configured={section.removable}
            removing={section.removing}
            onRemovingChange={section.setRemoving}
            onDiscard={() => section.setCredential({})}
          >
            {section.mode !== "forbidden" && (
              <SchemaFields
                secret
                autoFocus
                labelAction={
                  providerKeyLink(definition) && (
                    <ProviderKeyLink {...providerKeyLink(definition)!} />
                  )
                }
                schema={section.schema}
                requireFields={section.requireFields}
                value={section.credential}
                onChange={section.setCredential}
              />
            )}
          </CredentialRow>
        )}
        {definition?.supports_test && (
          <ConnectionTest
            action={async () =>
              providerTestResult(await api.testProvider(basis.value.id))
            }
            description="Reads from the provider without creating or starting anything."
            dirty={
              save.isPending ||
              name !== basis.value.name ||
              enabled !== basis.value.enabled ||
              Object.keys(section.credential).length > 0 ||
              section.removing
            }
            retry={() => void reload()}
          />
        )}
        <ProviderFacts
          hideDefaults
          configuration={configuration}
          schema={configSchema}
        />
      </ProviderGroup>
      <ErrorNotice error={save.error} retry={() => void reload()} />
      <FormActions
        pending={save.isPending}
        onCancel={close}
        label={t("Save changes")}
      />
    </ProviderEditor>
  );
}
