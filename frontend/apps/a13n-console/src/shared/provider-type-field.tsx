import type { ReactNode } from "react";
import { FormField } from "a13n-ui/patterns/form-field";
import { ReadOnlyField } from "a13n-ui/patterns/read-only-field";
import { SearchPicker } from "a13n-ui/patterns/search-picker";
import { useTranslation } from "react-i18next";
import { ProviderIcon } from "./provider-icon";

export function ProviderTypeField({
  definitions,
  value,
  onValueChange,
  readOnly = false,
  labelAction,
}: {
  definitions: readonly { type: string; display_name: string }[];
  value: string;
  onValueChange: (value: string) => void;
  readOnly?: boolean;
  labelAction?: ReactNode;
}) {
  const { t } = useTranslation();
  if (readOnly)
    return (
      <ReadOnlyField
        label={t("Provider type")}
        description={t("Provider type is fixed after creation.")}
      >
        <span className="flex items-center gap-2">
          <ProviderIcon type={value} />
          {definitions.find((item) => item.type === value)?.display_name ??
            value}
        </span>
      </ReadOnlyField>
    );
  return (
    <FormField label={t("Provider type")} labelAction={labelAction}>
      <SearchPicker
        label={t("Provider type")}
        placeholder={t("Search providers…")}
        emptyMessage={t("No matching providers")}
        value={value}
        onValueChange={onValueChange}
        groups={[
          {
            label: "",
            options: definitions.map((item) => ({
              value: item.type,
              label: item.display_name,
              keywords: [item.type],
              icon: <ProviderIcon key={item.type} type={item.type} />,
            })),
          },
        ]}
      />
    </FormField>
  );
}
