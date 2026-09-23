import { useState } from "react";
import { FormField, Textarea } from "a13n-ui";
import { useTranslation } from "react-i18next";
import type { components } from "../../service-client";
import { Section } from "../../shared/page";

type Config = components["schemas"]["AgentConfig"];
export function InteractionSettings({
  config,
  disabled,
  onChange,
}: {
  config: Config;
  disabled: boolean;
  onChange: (config: Config) => void;
}) {
  const { t } = useTranslation();
  const [tools, setTools] = useState(
    JSON.stringify(config.client_tools ?? [], null, 2),
  );
  return (
    <Section title={t("Questions and client tools")}>
      <label>
        <input
          type="checkbox"
          disabled={disabled}
          checked={config.user_questions ?? false}
          onChange={(event) =>
            onChange({ ...config, user_questions: event.target.checked })
          }
        />{" "}
        {t("Allow the agent to ask questions")}
      </label>
      <p>
        {t(
          "Questions pause the run until you send a message. Connection approvals are configured with each tool below.",
        )}
      </p>
      <FormField
        label={t("Client tool definitions (JSON)")}
        description={t(
          "Declare a name, description and object parameters_json_schema for each client tool. Results are entered manually in the Console; no browser code is executed.",
        )}
      >
        <Textarea
          rows={8}
          maxLength={262144}
          disabled={disabled}
          value={tools}
          spellCheck={false}
          onChange={(event) => {
            const text = event.target.value;
            setTools(text);
            try {
              const parsed: unknown = JSON.parse(text);
              if (!Array.isArray(parsed))
                throw new Error(
                  t("Enter a JSON array of client tool definitions."),
                );
              event.currentTarget.setCustomValidity("");
              onChange({ ...config, client_tools: parsed });
            } catch (error) {
              event.currentTarget.setCustomValidity(
                error instanceof Error ? error.message : t("Enter valid JSON."),
              );
            }
          }}
        />
      </FormField>
    </Section>
  );
}
