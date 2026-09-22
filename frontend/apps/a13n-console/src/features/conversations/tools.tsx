import { useTranslation } from "react-i18next";
import { isRecord } from "../../service-client/errors";
import { JsonView } from "../../shared/forms";
import styles from "./conversations.module.css";

export function toolEvents(events: Record<string, unknown>[]) {
  const calls = new Map<
    string,
    { id: string; name: string; arguments: string; result?: string }
  >();
  for (const event of events) {
    if (isRecord(event.metadata) && event.metadata.display === false) continue;
    const id = event.toolCallId;
    if (typeof id !== "string") continue;
    if (
      event.type === "TOOL_CALL_START" &&
      typeof event.toolCallName === "string"
    ) {
      calls.set(id, { id, name: event.toolCallName, arguments: "" });
    }
    const call = calls.get(id);
    if (!call) continue;
    if (event.type === "TOOL_CALL_ARGS" && typeof event.delta === "string")
      call.arguments += event.delta;
    if (event.type === "TOOL_CALL_RESULT" && typeof event.content === "string")
      call.result = event.content;
  }
  return [...calls.values()];
}
function structured(value: string): unknown {
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}
export function ToolObservations({
  events,
  settled,
}: {
  events: Record<string, unknown>[];
  settled: boolean;
}) {
  const { t } = useTranslation();
  return toolEvents(events).map((call) => (
    <details key={call.id} className={styles.tool} open>
      <summary>
        <span>{call.name}</span>
        <span className={styles.muted}>
          {t(
            call.result !== undefined
              ? "Result received"
              : settled
                ? "No recorded result"
                : "Calling tool…",
          )}
        </span>
      </summary>
      {call.arguments && (
        <>
          <p className={styles.muted}>{t("Arguments")}</p>
          <JsonView value={structured(call.arguments)} />
        </>
      )}
      {call.result !== undefined && (
        <>
          <p className={styles.muted}>{t("Tool result")}</p>
          <JsonView value={structured(call.result)} />
        </>
      )}
    </details>
  ));
}
