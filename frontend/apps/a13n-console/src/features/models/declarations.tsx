import {
  ChoiceField,
  DisclosureSection,
  Fieldset,
  FieldsetLegend,
  FormField,
  Input,
  ToggleGroup,
  ToggleGroupItem,
} from "a13n-ui";
import {
  ImageIcon,
  SpeakerHighIcon,
  VideoCameraIcon,
} from "@phosphor-icons/react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import styles from "../../shared/shared.module.css";
import modelStyles from "./models.module.css";

import {
  effortLabel,
  THINKING_EFFORTS,
  type ThinkingEffort,
} from "../../shared/thinking";

export type Declarations = Schema["ModelDeclarations"];
export type Pricing = NonNullable<Declarations["pricing"]>;

const CAPABILITY_META = [
  { value: "image_understanding", label: "Image", Icon: ImageIcon },
  { value: "video_understanding", label: "Video", Icon: VideoCameraIcon },
  { value: "audio_understanding", label: "Audio", Icon: SpeakerHighIcon },
] as const;
export type ModelCapability = (typeof CAPABILITY_META)[number]["value"];

export function emptyDeclarations(): Declarations {
  return {
    thinking_efforts: [],
    capabilities: [],
    context_window_tokens: null,
    max_output_tokens: null,
    structured_output: null,
    pricing: null,
  };
}

export function CapabilityIcons({
  capabilities,
}: {
  capabilities?: readonly string[] | null;
}) {
  const { t } = useTranslation();
  const known = CAPABILITY_META.filter((meta) =>
    capabilities?.includes(meta.value),
  );
  if (!known.length)
    return <span className="text-muted-foreground">{t("None")}</span>;
  return (
    <span className={modelStyles.capabilityIcons}>
      {known.map(({ value, label, Icon }) => (
        <span key={value} title={t(label)} aria-label={t(label)}>
          <Icon size={15} aria-hidden="true" />
        </span>
      ))}
    </span>
  );
}

const chipGroupClass = "flex-wrap gap-2";
const chipClass =
  "rounded-full border-input px-3 text-muted-foreground hover:bg-accent data-pressed:border-foreground data-pressed:bg-foreground data-pressed:text-background";

function positiveInt(text: string) {
  const value = Number(text);
  return Number.isInteger(value) && value > 0 ? value : null;
}
function price(text: string) {
  if (!text.trim()) return null;
  const value = Number(text);
  return Number.isFinite(value) && value >= 0 ? value : null;
}

export function DeclarationsFields({
  value,
  onChange,
  action,
}: {
  value: Declarations;
  onChange: (next: Declarations) => void;
  action?: ReactNode;
}) {
  const { t } = useTranslation();
  const efforts = value.thinking_efforts ?? [];
  const capabilities = value.capabilities ?? [];
  const pricing = value.pricing ?? null;
  const setPricing = (key: keyof Pricing, rate: number | null) => {
    const next: Pricing = {
      input: null,
      output: null,
      cache_read: null,
      cache_write: null,
      ...(pricing ?? {}),
      [key]: rate,
    };
    onChange({
      ...value,
      pricing: Object.values(next).every((rate) => rate == null) ? null : next,
    });
  };
  return (
    <div className={modelStyles.declarations}>
      {action}
      <Fieldset className={modelStyles.choiceSet}>
        <FieldsetLegend className={modelStyles.choiceLegend}>
          {t("Agent effort choices")}
        </FieldsetLegend>
        <p className={modelStyles.choiceHint}>
          {t("Reasoning levels offered to agents using this model.")}
        </p>
        <ToggleGroup
          multiple
          size="sm"
          aria-label={t("Agent effort choices")}
          className={chipGroupClass}
          value={efforts}
          onValueChange={(next) =>
            onChange({ ...value, thinking_efforts: next as ThinkingEffort[] })
          }
        >
          {THINKING_EFFORTS.map((effort) => (
            <ToggleGroupItem key={effort} value={effort} className={chipClass}>
              {effortLabel(t, effort)}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      </Fieldset>
      <Fieldset className={modelStyles.choiceSet}>
        <FieldsetLegend className={modelStyles.choiceLegend}>
          {t("Input capabilities")}
        </FieldsetLegend>
        <p className={modelStyles.choiceHint}>
          {t("Media this model can understand.")}
        </p>
        <ToggleGroup
          multiple
          size="sm"
          aria-label={t("Input capabilities")}
          className={chipGroupClass}
          value={capabilities}
          onValueChange={(next) =>
            onChange({ ...value, capabilities: next as ModelCapability[] })
          }
        >
          {CAPABILITY_META.map(({ value: capability, label, Icon }) => (
            <ToggleGroupItem
              key={capability}
              value={capability}
              className={chipClass}
            >
              <Icon size={14} aria-hidden="true" />
              {t(label)}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      </Fieldset>
      <div className={styles.twoColumns}>
        <FormField label={t("Context window (tokens)")}>
          <Input
            type="number"
            min={1}
            step={1}
            placeholder={t("Unknown")}
            value={value.context_window_tokens ?? ""}
            onChange={(event) =>
              onChange({
                ...value,
                context_window_tokens: positiveInt(event.target.value),
              })
            }
          />
        </FormField>
        <FormField label={t("Max output (tokens)")}>
          <Input
            type="number"
            min={1}
            step={1}
            placeholder={t("Unknown")}
            value={value.max_output_tokens ?? ""}
            onChange={(event) =>
              onChange({
                ...value,
                max_output_tokens: positiveInt(event.target.value),
              })
            }
          />
        </FormField>
      </div>
      <ChoiceField
        label={t("Structured output")}
        description={t(
          "Whether the model can return schema-constrained output.",
        )}
        value={
          value.structured_output === true
            ? "supported"
            : value.structured_output === false
              ? "unsupported"
              : "unknown"
        }
        onValueChange={(next) =>
          onChange({
            ...value,
            structured_output:
              next === "supported"
                ? true
                : next === "unsupported"
                  ? false
                  : null,
          })
        }
        options={[
          { value: "unknown", label: t("Unknown") },
          { value: "supported", label: t("Supported") },
          { value: "unsupported", label: t("Not supported") },
        ]}
      />
      <DisclosureSection
        title={t("Pricing")}
        summary={t("USD per million tokens")}
      >
        <div className={styles.twoColumns}>
          <FormField label={t("Input")}>
            <Input
              type="number"
              min={0}
              step="any"
              placeholder={t("Unknown")}
              value={pricing?.input ?? ""}
              onChange={(event) =>
                setPricing("input", price(event.target.value))
              }
            />
          </FormField>
          <FormField label={t("Output")}>
            <Input
              type="number"
              min={0}
              step="any"
              placeholder={t("Unknown")}
              value={pricing?.output ?? ""}
              onChange={(event) =>
                setPricing("output", price(event.target.value))
              }
            />
          </FormField>
          <FormField label={t("Cache read")}>
            <Input
              type="number"
              min={0}
              step="any"
              placeholder={t("Unknown")}
              value={pricing?.cache_read ?? ""}
              onChange={(event) =>
                setPricing("cache_read", price(event.target.value))
              }
            />
          </FormField>
          <FormField label={t("Cache write")}>
            <Input
              type="number"
              min={0}
              step="any"
              placeholder={t("Unknown")}
              value={pricing?.cache_write ?? ""}
              onChange={(event) =>
                setPricing("cache_write", price(event.target.value))
              }
            />
          </FormField>
        </div>
      </DisclosureSection>
    </div>
  );
}
