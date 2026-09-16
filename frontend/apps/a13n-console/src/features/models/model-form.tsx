import { ManageProvidersLink } from "../providers/manage-link";
import { ResourceReference } from "../../shared/resource-reference";
import { ConnectionTest } from "./connection-test";
import { ProviderIcon } from "../../shared/provider-icon";
import { suggestedKey } from "./model-options";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  ChoiceField,
  FormField,
  Input,
  ReadOnlyField,
  SearchPicker,
  Switch,
  Tabs,
  TabsList,
  TabsTab,
} from "a13n-ui";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { allPages, data, type Schema } from "../../shared/api";
import { ErrorNotice, InlineLoading, Loading } from "../../shared/feedback";
import { FormActions } from "../../shared/form";
import styles from "../../shared/shared.module.css";
import { jsonObject, validateSettings } from "../../shared/validation";
import { modelApi, type ModelScope } from "./api";
import { DeclarationsFields, emptyDeclarations } from "./declarations";
import {
  encodeThinking,
  effortLabel,
  THINKING_EFFORTS,
  withThinking,
} from "../../shared/thinking";
import { ModelIcon } from "./model-icon";
import { ModelParameters } from "./model-parameters";
import { ProviderSetup } from "./provider-setup";
import modelStyles from "./models.module.css";

const CUSTOM_BASE_MODEL = "__custom__";

function FormSection({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <section className={modelStyles.formSection}>
      <header className={modelStyles.formSectionHeader}>
        <strong>{title}</strong>
        {description && <span>{description}</span>}
      </header>
      <div className={modelStyles.formSectionBody}>{children}</div>
    </section>
  );
}

export function ModelForm({
  scope,
  resource,
  providerId,
  candidate,
  close,
  reload,
  onSaved,
}: {
  scope: ModelScope;
  resource?: { value: Schema["Model"]; etag?: string };
  providerId?: string;
  candidate?: Schema["ModelCandidate"];
  close: () => void;
  reload: () => Promise<void>;
  onSaved?: (model: Schema["Model"]) => void;
}) {
  const [original] = useState(resource),
    client = useClient(),
    { t } = useTranslation(),
    cache = useQueryClient(),
    api = modelApi(client, scope);
  const [provider, setProvider] = useState(
      original?.value.provider_id ?? providerId ?? "",
    ),
    [choosingProvider, setChoosingProvider] = useState(
      !original && !providerId,
    ),
    [upstream, setUpstream] = useState(
      original?.value.upstream_model ?? candidate?.upstream_model ?? "",
    ),
    [name, setName] = useState(
      original?.value.name ??
        candidate?.display_name ??
        candidate?.upstream_model ??
        "",
    ),
    [key, setKey] = useState(
      original?.value.key ?? suggestedKey(candidate?.upstream_model ?? ""),
    ),
    [nameEdited, setNameEdited] = useState(!!original),
    [keyEdited, setKeyEdited] = useState(!!original),
    [apiEdited, setApiEdited] = useState(!!original),
    [modelApiKey, setModelApiKey] = useState(
      original?.value.model_api ?? candidate?.suggested_model_api ?? "",
    ),
    [settingsText, setSettingsText] = useState(
      JSON.stringify(
        original?.value.settings ?? candidate?.suggested_settings ?? {},
        null,
        2,
      ),
    ),
    [description, setDescription] = useState(original?.value.description ?? ""),
    [enabled, setEnabled] = useState(original?.value.enabled ?? true),
    [parameterError, setParameterError] = useState<string>(),
    [manual, setManual] = useState(!!original || !!candidate),
    [settledUpstream, setSettledUpstream] = useState(upstream);
  const [baseModel, setBaseModel] = useState<string | null | undefined>(
      original ? (original.value.base_model ?? null) : undefined,
    ),
    [baseModelManual, setBaseModelManual] = useState(!!original),
    [declarations, setDeclarations] = useState<Schema["ModelDeclarations"]>(
      () => original?.value.declarations ?? emptyDeclarations(),
    ),
    [declarationsTouched, setDeclarationsTouched] = useState(!!original);
  const providers = useQuery({
    queryKey: ["model-provider-choices", scope.kind, scope.id],
    queryFn: ({ signal }) =>
      allPages((cursor) => api.providers(signal, cursor)),
  });
  const definitions = useQuery({
    queryKey: ["model-provider-types"],
    queryFn: ({ signal }) =>
      client.http.GET("/api/v1/model-provider-types", { signal }).then(data),
  });
  const selectedProvider = providers.data?.find((item) => item.id === provider);
  const definition = definitions.data?.items.find(
    (item) => item.type === selectedProvider?.type,
  );
  const callingApi = modelApiKey || definition?.default_model_api || "";
  const discovery = useQuery({
    queryKey: ["model-discovery", scope.kind, scope.id, provider],
    queryFn: () => api.discover(provider),
    enabled:
      !!provider &&
      !choosingProvider &&
      !manual &&
      !!definition?.supports_model_discovery,
    retry: false,
  });
  useEffect(() => {
    const timer = setTimeout(() => setSettledUpstream(upstream.trim()), 400);
    return () => clearTimeout(timer);
  }, [upstream]);
  const baseModels = useQuery({
    queryKey: ["base-models"],
    queryFn: ({ signal }) =>
      client.http.GET("/api/v1/base-models", { signal }).then(data),
  });
  const identityChanged =
    !!original &&
    (settledUpstream !== original.value.upstream_model ||
      callingApi !== original.value.model_api);
  const suggestions = useQuery({
    queryKey: [
      "model-catalog-suggestions",
      scope.kind,
      scope.id,
      provider,
      settledUpstream,
      callingApi,
      !original && baseModelManual ? (baseModel ?? "none") : "auto",
    ],
    queryFn: () =>
      api.suggestions({
        provider_id: provider,
        upstream_model: settledUpstream,
        model_api: callingApi || undefined,
        ...(!original && baseModelManual
          ? { base_model: baseModel ?? null }
          : {}),
      }),
    enabled:
      !!provider &&
      !!settledUpstream &&
      !!callingApi &&
      settledUpstream === upstream.trim() &&
      (!original || identityChanged),
    retry: false,
  });
  const currentSuggestions =
    settledUpstream === upstream.trim() ? suggestions.data?.items : undefined;
  // A resolved match proposes the base reference and declaration defaults;
  // anything the user already chose or edited is never overwritten.
  useEffect(() => {
    if (original || !suggestions.data?.items?.length) return;
    const items = suggestions.data.items;
    const pick =
      items.length === 1
        ? items[0]
        : items.find((item) => item.base_model === baseModel);
    if (!pick) return;
    if (!baseModelManual) setBaseModel(pick.base_model);
    if (!declarationsTouched)
      setDeclarations({ ...emptyDeclarations(), ...pick.declarations });
    if (!apiEdited && pick.model_api) setModelApiKey(pick.model_api);
  }, [
    suggestions.data,
    original,
    baseModel,
    baseModelManual,
    declarationsTouched,
    apiEdited,
  ]);
  function chooseUpstream(
    value: string,
    suggestion?: Schema["ModelCandidate"],
  ) {
    setUpstream(value);
    if (!nameEdited) setName((suggestion?.display_name ?? value).slice(0, 128));
    if (!keyEdited) setKey(suggestedKey(value));
    if (suggestion) {
      if (!apiEdited) setModelApiKey(suggestion.suggested_model_api);
      if (settingsText.trim() === "{}")
        setSettingsText(JSON.stringify(suggestion.suggested_settings, null, 2));
    }
  }
  function chooseProvider(value: string) {
    if (value !== provider) {
      setProvider(value);
      setModelApiKey("");
      setApiEdited(false);
      setUpstream("");
      setSettledUpstream("");
      setSettingsText("{}");
      setBaseModel(undefined);
      setBaseModelManual(false);
      setDeclarations(emptyDeclarations());
      setDeclarationsTouched(false);
      if (!nameEdited) setName("");
      if (!keyEdited) setKey("");
      setManual(false);
    }
    setChoosingProvider(false);
  }
  function chooseBaseModel(value: string) {
    setBaseModelManual(true);
    setBaseModel(value === CUSTOM_BASE_MODEL ? null : value);
    const match = currentSuggestions?.find((item) => item.base_model === value);
    if (match) {
      if (!apiEdited && match.model_api) setModelApiKey(match.model_api);
      if (!declarationsTouched)
        setDeclarations({ ...emptyDeclarations(), ...match.declarations });
    }
  }
  const save = useMutation({
    mutationFn: async () => {
      let settings: ReturnType<typeof jsonObject>;
      try {
        settings = jsonObject(settingsText);
        if (definition)
          validateSettings(definition.settings_schemas[callingApi], settings);
        setParameterError(undefined);
      } catch (error) {
        setParameterError(
          error instanceof Error ? error.message : t("Invalid JSON"),
        );
        throw error;
      }
      const body = {
        name,
        upstream_model: upstream.trim(),
        model_api: callingApi,
        settings,
        description: description || null,
        enabled,
        declarations,
        ...(original || baseModel !== undefined
          ? { base_model: baseModel ?? null }
          : {}),
      };
      if (!original)
        return api.createModel({ ...body, key, provider_id: provider });
      if (!original.etag)
        throw new Error(
          t("Version information is unavailable. Reload this page."),
        );
      return api.updateModel(original.value.id, original.etag, body);
    },
    onSuccess: (model) => {
      void cache.invalidateQueries();
      onSaved?.(model);
      close();
    },
  });
  const dirty =
    !!original &&
    (upstream.trim() !== original.value.upstream_model ||
      callingApi !== original.value.model_api ||
      name !== original.value.name ||
      description !== (original.value.description ?? "") ||
      enabled !== original.value.enabled ||
      settingsText !== JSON.stringify(original.value.settings, null, 2) ||
      (baseModel ?? null) !== (original.value.base_model ?? null) ||
      JSON.stringify(declarations) !==
        JSON.stringify({
          ...emptyDeclarations(),
          ...original.value.declarations,
        }));
  const parsedSettings = useMemo(() => {
    try {
      return jsonObject(settingsText);
    } catch {
      return undefined;
    }
  }, [settingsText]);
  const reasoning = encodeThinking(parsedSettings?.thinking);
  const reasoningOptions = [
    { value: "unset", label: t("Provider default") },
    { value: "off", label: t("Off") },
    ...THINKING_EFFORTS.map((effort) => ({
      value: effort as string,
      label: effortLabel(t, effort),
    })),
    ...(reasoning === "on" ? [{ value: "on", label: t("On") }] : []),
    ...(!["unset", "off", "on", ...THINKING_EFFORTS].includes(reasoning)
      ? [{ value: reasoning, label: reasoning }]
      : []),
  ];
  const suggestedBaseModels = (currentSuggestions ?? []).filter(
    (item, index, items) =>
      items.findIndex((other) => other.base_model === item.base_model) ===
      index,
  );
  const knownBaseModels = baseModels.data?.items ?? [];
  const baseModelGroups = [
    ...(suggestedBaseModels.length
      ? [
          {
            label: t("Suggested"),
            options: suggestedBaseModels.map((item) => ({
              value: item.base_model,
              label: item.base_model,
              description: item.model_api_label ?? undefined,
            })),
          },
        ]
      : []),
    {
      label: t("All base models"),
      options: [
        { value: CUSTOM_BASE_MODEL, label: t("Custom (no reference)") },
        ...knownBaseModels.map((item) => ({
          value: item.base_model,
          label: item.base_model,
          description: item.model_api_label ?? undefined,
        })),
        ...(baseModel &&
        !knownBaseModels.some((item) => item.base_model === baseModel) &&
        !suggestedBaseModels.some((item) => item.base_model === baseModel)
          ? [{ value: baseModel, label: baseModel }]
          : []),
      ],
    },
  ];
  const freshSuggestion =
    original && identityChanged && currentSuggestions?.length === 1
      ? currentSuggestions[0]
      : undefined;
  const applySuggestion =
    freshSuggestion &&
    (freshSuggestion.base_model !== (baseModel ?? null) ||
      JSON.stringify({
        ...emptyDeclarations(),
        ...freshSuggestion.declarations,
      }) !== JSON.stringify(declarations))
      ? () => {
          setBaseModel(freshSuggestion.base_model);
          setDeclarations({
            ...emptyDeclarations(),
            ...freshSuggestion.declarations,
          });
          setDeclarationsTouched(true);
        }
      : undefined;
  if (choosingProvider)
    return (
      <ProviderSetup
        onCancel={close}
        scope={scope}
        providers={providers.data}
        definitions={definitions.data?.items}
        value={provider}
        error={providers.error ?? definitions.error}
        onSelect={chooseProvider}
        onCreated={(item, preferredApi) => {
          cache.setQueryData<Schema["ModelProvider"][]>(
            ["model-provider-choices", scope.kind, scope.id],
            (items) => [
              ...(items ?? []).filter((value) => value.id !== item.id),
              item,
            ],
          );
          chooseProvider(item.id);
          if (preferredApi) {
            setModelApiKey(preferredApi);
            setApiEdited(true);
          }
        }}
      />
    );
  const identityFields = (
    <section
      className={
        original ? modelStyles.editIdentity : modelStyles.identityFields
      }
    >
      <div className={original ? styles.stack : styles.twoColumns}>
        <FormField
          label={t("Name")}
          labelAction={
            original && (
              <ResourceReference
                id={original.value.id}
                resourceKey={original.value.key}
              />
            )
          }
        >
          <Input
            required
            maxLength={128}
            value={name}
            onChange={(event) => {
              setName(event.target.value);
              setNameEdited(true);
            }}
          />
        </FormField>
        {!original && (
          <FormField
            label={t("Model key")}
            description={t("Used by agents. Cannot be changed later.")}
          >
            <Input
              required
              maxLength={128}
              value={key}
              onChange={(event) => {
                setKey(event.target.value);
                setKeyEdited(true);
              }}
            />
          </FormField>
        )}
      </div>
      <FormField label={t("Description")}>
        <Input
          value={description}
          placeholder={t("Optional")}
          onChange={(event) => setDescription(event.target.value)}
        />
      </FormField>
    </section>
  );
  const connectionFields = (
    <section className={modelStyles.connectionFields}>
      <div
        className={`${modelStyles.connectionSummary} ${modelStyles.providerSummary}`}
      >
        {selectedProvider && (
          <ProviderIcon
            key={selectedProvider.type}
            type={selectedProvider.type}
          />
        )}
        <div>
          <strong>
            {selectedProvider?.name ?? <InlineLoading width="8rem" />}
          </strong>
          {typeof selectedProvider?.configuration.base_url === "string" && (
            <span>{selectedProvider.configuration.base_url}</span>
          )}
        </div>
        {!original && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setChoosingProvider(true)}
          >
            {t("Change")}
          </Button>
        )}
      </div>
      {selectedProvider && (
        <ReadOnlyField
          label={t("Session affinity header")}
          description={t(
            "Inherited from the provider connection. A stable UUID derived from the current Thread ID is supplied automatically; edit the provider to change the header.",
          )}
        >
          <span>
            {String(
              selectedProvider.configuration.session_affinity_header ??
                t("Disabled"),
            )}
          </span>
          <ManageProvidersLink
            category="models"
            scope={selectedProvider.workspace_id ? "workspace" : "organization"}
          />
        </ReadOnlyField>
      )}
      {!original && definition?.supports_model_discovery && (
        <Tabs
          value={manual ? "manual" : "catalog"}
          onValueChange={(value) => setManual(value === "manual")}
        >
          <TabsList aria-label={t("Model source")}>
            <TabsTab value="catalog">{t("From catalog")}</TabsTab>
            <TabsTab value="manual">{t("Enter model ID")}</TabsTab>
          </TabsList>
        </Tabs>
      )}
      <div className={styles.twoColumns}>
        {!original && !manual && definition?.supports_model_discovery ? (
          <div className={styles.stack}>
            {discovery.isPending ? (
              <Loading variant="list" rows={3} />
            ) : discovery.data?.items.length ? (
              <FormField label={t("Model")}>
                <SearchPicker
                  label={t("Model")}
                  placeholder={t("Choose a model…")}
                  emptyMessage={t(
                    "No models found. You can still add a model manually.",
                  )}
                  value={upstream}
                  onValueChange={(value) =>
                    chooseUpstream(
                      value,
                      discovery.data?.items.find(
                        (item) => item.upstream_model === value,
                      ),
                    )
                  }
                  groups={[
                    {
                      label: t("Models"),
                      options: discovery.data.items.map((item) => ({
                        value: item.upstream_model,
                        label: item.display_name ?? item.upstream_model,
                        description:
                          item.display_name &&
                          item.display_name !== item.upstream_model
                            ? item.upstream_model
                            : undefined,
                      })),
                    },
                  ]}
                />
              </FormField>
            ) : (
              <p className={styles.muted}>
                {t("Catalog unavailable. Enter a model ID to continue.")}
              </p>
            )}
            {(discovery.error ||
              (!discovery.data?.items.length && !discovery.isPending)) && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className={modelStyles.catalogFallback}
                onClick={() => setManual(true)}
              >
                {t("Enter model ID")}
              </Button>
            )}
          </div>
        ) : (
          <FormField label={t("Upstream model")}>
            <Input
              required
              value={upstream}
              placeholder="e.g. gpt-4.1"
              maxLength={256}
              onChange={(event) => chooseUpstream(event.target.value)}
            />
          </FormField>
        )}
        {(definition?.supported_model_apis.length ?? 0) > 1 && (
          <ChoiceField
            label={t("API")}
            value={callingApi}
            onValueChange={(value) => {
              setModelApiKey(value);
              setApiEdited(true);
            }}
            options={
              definition?.supported_model_apis.map((value) => ({
                value,
                label: definition.model_api_labels[value] ?? value,
              })) ?? []
            }
          />
        )}
      </div>
      {original && (
        <ConnectionTest
          compact
          action={() => api.testModel(original.value.id)}
          dirty={dirty || save.isPending}
          description="May consume quota or incur cost."
        />
      )}
    </section>
  );
  return (
    <form
      className={`${modelStyles.modelForm} ${original ? modelStyles.editForm : ""}`}
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      {original && (
        <>
          <div className={modelStyles.modelIdentity}>
            <div className={modelStyles.modelMark}>
              <ModelIcon
                upstream={upstream}
                provider={selectedProvider?.type}
              />
            </div>
            <div className={modelStyles.modelHeading}>
              <h3>{original.value.name}</h3>
              <span>{original.value.key}</span>
            </div>
            <div className={modelStyles.modelStatus}>
              <Switch
                id="model-enabled"
                aria-labelledby="model-enabled-label"
                checked={enabled}
                onCheckedChange={setEnabled}
              />
              <label id="model-enabled-label" htmlFor="model-enabled">
                {t(enabled ? "Enabled" : "Disabled")}
              </label>
            </div>
          </div>
        </>
      )}
      {original ? (
        <>
          <FormSection
            title={t("General")}
            description={t("How this model appears to agents.")}
          >
            {identityFields}
          </FormSection>
          <FormSection
            title={t("Connection")}
            description={t("Provider, upstream model, and API.")}
          >
            {connectionFields}
          </FormSection>
        </>
      ) : (
        <>
          <FormSection
            title={t("Connection")}
            description={t("Provider, upstream model, and API.")}
          >
            {connectionFields}
          </FormSection>
          <FormSection
            title={t("General")}
            description={t("How this model appears to agents.")}
          >
            {identityFields}
          </FormSection>
        </>
      )}

      <FormSection
        title={t("Model defaults")}
        description={t("Capabilities and reasoning agents inherit.")}
      >
        <FormField
          label={t("Base model")}
          description={t(
            "Known model used for defaults and capabilities. The upstream name is still sent to the provider.",
          )}
        >
          <SearchPicker
            label={t("Base model")}
            placeholder={t("Match automatically")}
            emptyMessage={t("No matching base models")}
            value={baseModel === null ? CUSTOM_BASE_MODEL : baseModel}
            onValueChange={chooseBaseModel}
            groups={baseModelGroups}
          />
        </FormField>
        <ChoiceField
          label={t("Default reasoning")}
          description={t(
            "Applied to runs unless an agent or run overrides it.",
          )}
          disabled={!parsedSettings}
          value={reasoning}
          onValueChange={(value) =>
            setSettingsText(
              JSON.stringify(
                withThinking(parsedSettings ?? {}, value),
                null,
                2,
              ),
            )
          }
          options={reasoningOptions}
        />
        <DeclarationsFields
          value={declarations}
          onChange={(next) => {
            setDeclarations(next);
            setDeclarationsTouched(true);
          }}
          action={
            applySuggestion && (
              <p className={modelStyles.suggestionNote}>
                <span>
                  {t("Catalog defaults are available for this identity.")}
                </span>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={applySuggestion}
                >
                  {t("Apply")}
                </Button>
              </p>
            )
          }
        />
      </FormSection>

      <ModelParameters
        text={settingsText}
        onChange={(next) => {
          setSettingsText(next);
          setParameterError(undefined);
        }}
        error={parameterError}
        schema={definition?.settings_schemas[callingApi]}
      />
      <ErrorNotice
        error={parameterError ? undefined : save.error}
        retry={original ? () => void reload() : undefined}
      />
      <FormActions
        onCancel={close}
        pending={save.isPending}
        disabled={
          !upstream.trim() ||
          !name.trim() ||
          !key.trim() ||
          (!!original && !dirty)
        }
        label={t(original ? "Save changes" : "Add model")}
      />
    </form>
  );
}
