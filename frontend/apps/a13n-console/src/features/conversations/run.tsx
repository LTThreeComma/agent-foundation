import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "a13n-ui";
import { useScope } from "../../layout/workspace";
import { data } from "../../service-client";
import { ErrorNotice, StatePill } from "../../shared/feedback";
import { Pagination, useCursor } from "../../shared/collection";
import { MarkdownContent } from "../../shared/markdown";
import { ToolObservations } from "./tools";
import { isRecord } from "../../service-client/errors";
import { observeRun, type Observation } from "./observe";
import { inputContent } from "./input-content";
import { useRunOpen } from "./transcript/debug/collapse";
import { useViewLevel } from "./transcript/debug/view";
import styles from "./conversations.module.css";

export function RunConversation({
  runId,
  onUpdate,
}: {
  runId: string;
  onUpdate: () => void;
}) {
  const { client, path, cache } = useScope();
  const { t } = useTranslation();
  const page = useCursor();
  const { level } = useViewLevel();
  const [open] = useRunOpen(runId);
  const [observation, setObservation] = useState<Observation>();
  const [error, setError] = useState<unknown>(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setObservation(undefined);
    setError(null);
    void observeRun(
      client,
      path.workspace_id,
      runId,
      page.cursor,
      controller.signal,
      (value) => {
        setObservation(value);
        if (value.snapshot.complete) onUpdate();
      },
    ).catch((failure) => {
      if (!controller.signal.aborted) setError(failure);
    });
    return () => controller.abort();
  }, [client, path.workspace_id, runId, page.cursor, retry, onUpdate]);
  const snapshot = observation?.snapshot;
  const usage = useQuery({
    queryKey: [...cache, "usage", runId, snapshot?.complete],
    queryFn: async ({ signal }) =>
      data(
        await client.http.GET("/api/v1/workspaces/{workspace_id}/usage", {
          params: { path, query: { run_id: runId } },
          signal,
        }),
      ),
  });
  return (
    <section
      data-run={runId}
      aria-label={t("Run conversation")}
      className={styles.transcript}
    >
      <ErrorNotice error={error} />
      {!!error && (
        <Button
          variant="outline"
          onClick={() => setRetry((value) => value + 1)}
        >
          {t("Reconnect")}
        </Button>
      )}
      {snapshot && (
        <>
          <div className={styles.runStatus}>
            <StatePill state={snapshot.status} />
            <span>
              {t(
                observation.connection === "recovering"
                  ? "Refreshing durable history…"
                  : observation.connection === "live"
                    ? "Receiving updates"
                    : "History finalized",
              )}
            </span>
          </div>
          <div hidden={level === "debug" && !open}>
            {snapshot.inputs.map((input) => (
              <article key={input.id} className={styles.input}>
                <div className={styles.messageHeading}>
                  {t("You")} <StatePill state={input.status} />
                </div>
                {input.payload && "content" in input.payload ? (
                  input.payload.content.map((part, index) => (
                    <p key={index}>{inputContent(part)}</p>
                  ))
                ) : (
                  <p>{t("Control response")}</p>
                )}
              </article>
            ))}
            <Pagination page={page} next={snapshot.next_input_cursor} />
            {snapshot.segments.map((segment, index) => {
              const live =
                index === snapshot.segments.length - 1 ? observation.live : [];
              const known = new Set(
                segment.items?.flatMap((item) =>
                  typeof item.id === "string" ? [item.id] : [],
                ) ?? [],
              );
              return (
                <section
                  key={segment.attempt_id}
                  className={
                    segment.interrupted ? styles.interrupted : styles.attempt
                  }
                  aria-label={`${t("Attempt")} ${segment.attempt_number}`}
                >
                  {segment.interrupted && (
                    <p>{t("Interrupted attempt — retained for reference")}</p>
                  )}
                  {level === "debug" && (
                    <ToolObservations
                      settled={
                        snapshot.complete || segment.interrupted === true
                      }
                      events={[
                        ...(segment.items ?? []).flatMap((item) =>
                          item.type === "event" && isRecord(item.event)
                            ? [item.event]
                            : [],
                        ),
                        ...(index === snapshot.segments.length - 1
                          ? (observation.toolEvents ?? [])
                          : []),
                      ]}
                    />
                  )}
                  {segment.items?.map((item, itemIndex) => {
                    if (
                      (level === "chat" && item.type === "reasoning") ||
                      (item.type !== "text" && item.type !== "reasoning") ||
                      typeof item.text !== "string"
                    )
                      return null;
                    const text =
                      item.text +
                      (live.find(
                        (message) =>
                          message.id === item.id && message.type === item.type,
                      )?.text ?? "");
                    return (
                      <article
                        key={typeof item.id === "string" ? item.id : itemIndex}
                        className={styles.answer}
                      >
                        <div className={styles.messageHeading}>
                          {t(
                            item.type === "reasoning"
                              ? "Reasoning"
                              : "Assistant",
                          )}
                        </div>
                        <MarkdownContent text={text} />
                      </article>
                    );
                  })}
                  {live
                    .filter(
                      (message) =>
                        !known.has(message.id) &&
                        (level === "debug" || message.type !== "reasoning"),
                    )
                    .map((message) => (
                      <article key={message.id} className={styles.answer}>
                        <div className={styles.messageHeading}>
                          {t(
                            message.type === "reasoning"
                              ? "Reasoning"
                              : "Assistant",
                          )}
                        </div>
                        <MarkdownContent text={message.text} />
                      </article>
                    ))}
                </section>
              );
            })}
            {snapshot.failure && (
              <p role="alert">
                {typeof snapshot.failure.message === "string"
                  ? snapshot.failure.message
                  : t("The run did not complete.")}
              </p>
            )}
            <p className={styles.muted}>
              {t("Usage and cost")} ·{" "}
              {usage.data && usage.data.current.requests > 0
                ? `${usage.data.current.input_tokens ?? t("Unknown")} ${t("input tokens")} · ${usage.data.current.output_tokens ?? t("Unknown")} ${t("output tokens")}`
                : t("Unknown")}{" "}
              · {t("Cost")}: {t("Unknown")}
            </p>
          </div>
        </>
      )}
    </section>
  );
}
