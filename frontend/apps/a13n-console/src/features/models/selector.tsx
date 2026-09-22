import { useQuery } from "@tanstack/react-query";
import { ChoiceField } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useScope } from "../../layout/workspace";
import { data } from "../../service-client";
import { Pagination, useCursor } from "../../shared/collection";
import { ErrorNotice } from "../../shared/feedback";

export function ModelSelector({
  value,
  onChange,
  disabled,
}: {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  const { client, cache, workspace } = useScope();
  const { t } = useTranslation();
  const page = useCursor();
  const query = useQuery({
    queryKey: [...cache, "models", page.cursor],
    queryFn: async ({ signal }) =>
      data(
        await client.http.GET(
          "/api/v1/organizations/{organization_id}/models",
          {
            params: {
              path: { organization_id: workspace.organization_id },
              query: {
                workspace_id: workspace.id,
                limit: 50,
                cursor: page.cursor,
              },
            },
            signal,
          },
        ),
      ),
  });
  const selected = useQuery({
    queryKey: [...cache, "model", value],
    enabled: !!value && !query.data?.items.some((item) => item.id === value),
    queryFn: async ({ signal }) =>
      data(
        await client.http.GET(
          "/api/v1/organizations/{organization_id}/models/{model_id}",
          {
            params: {
              path: {
                organization_id: workspace.organization_id,
                model_id: value,
              },
            },
            signal,
          },
        ),
      ),
  });
  const options =
    query.data?.items.map((item) => ({
      value: item.id,
      label: item.name,
      disabled: !item.enabled,
    })) ?? [];
  if (value && !options.some((item) => item.value === value))
    options.unshift({
      value,
      label: selected.data?.name ?? t("Saved model (unavailable)"),
      disabled: false,
    });
  return (
    <>
      <ChoiceField
        label={t("Model")}
        placeholder={t("Choose a model")}
        required
        value={value}
        options={options}
        onValueChange={onChange}
        disabled={disabled}
      />
      <ErrorNotice error={query.error ?? selected.error} />
      <Pagination page={page} next={query.data?.next_cursor} />
    </>
  );
}
