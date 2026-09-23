import { FormField, Input } from "a13n-ui";
import { useTranslation } from "react-i18next";

/** The working directory a mount uses on a registered device. */
export function DeviceDirectory({
  value,
  onChange,
}: {
  value: string;
  onChange: (path: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <section className="flex flex-col gap-3" aria-label={t("Device directory")}>
      <FormField
        label={t("Working directory")}
        description={t(
          "Enter an absolute Device path. This is a working directory, not a filesystem sandbox.",
        )}
      >
        <Input
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
      </FormField>
    </section>
  );
}
