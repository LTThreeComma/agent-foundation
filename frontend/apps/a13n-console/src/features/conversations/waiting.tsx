import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button, FormField, Textarea } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useScope } from "../../layout/workspace";
import { data, type components } from "../../service-client";
import { isRecord } from "../../service-client/errors";
import { useIdempotency } from "../../shared/idempotency";
import { ErrorNotice } from "../../shared/feedback";
import styles from "./conversations.module.css";

type Submitted = components["schemas"]["Submitted"];
type Feedback = components["schemas"]["FeedbackSubmission"];

export function WaitingPanel({
  threadId,
  runId,
  busy,
  lastRunId,
  onSubmitted,
}: {
  threadId: string;
  runId: string;
  busy: boolean;
  lastRunId: string | null;
  onSubmitted: (result: Submitted) => void;
}) {
  const { client, path, cache, workspace } = useScope();
  const { t } = useTranslation();
  const request = useIdempotency();
  const [approved, setApproved] = useState<Record<string, boolean>>({});
  const [results, setResults] = useState<Record<string, string>>({});
  const [pending, setPending] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    if (!busy && lastRunId !== runId) {
      setSent(false);
      request.reset();
    }
  }, [busy, lastRunId, runId]);
  const query = useQuery({
    queryKey: [...cache, "waiting", runId],
    queryFn: async ({ signal }) =>
      data(
        await client.http.GET(
          "/api/v1/workspaces/{workspace_id}/runs/{run_id}",
          {
            params: { path: { ...path, run_id: runId } },
            signal,
          },
        ),
      ),
  });
  if (!query.data) return <ErrorNotice error={query.error} />;
  const run = query.data;
  if (run.status !== "waiting" || !run.pending?.length) return null;
  const control = run.pending.some((item) => item.kind !== "user_input");
  const disabled =
    busy || pending || sent || !workspace.permissions.includes("run");
  return (
    <section
      className={styles.waiting}
      aria-label={t("Waiting for your response")}
    >
      <div>
        <h2>{t("Waiting for your response")}</h2>
        <p>
          {t(
            busy || sent
              ? "A response has been submitted. Follow the current run above."
              : control
                ? "Review the requests below. Approvals left unchecked will be denied; empty client results receive no response."
                : "Reply in the message box below to continue.",
          )}
        </p>
      </div>
      <ErrorNotice error={error} />
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          if (disabled || !control) return;
          setError(null);
          setPending(true);
          try {
            const answers: NonNullable<Feedback["answers"]> = [];
            for (const item of run.pending ?? []) {
              if (item.kind === "approval" && approved[item.tool_call_id]) {
                answers.push({
                  tool_call_id: item.tool_call_id,
                  action: "approve",
                });
              } else if (
                item.kind === "client_tool" &&
                results[item.tool_call_id]?.trim()
              ) {
                answers.push({
                  tool_call_id: item.tool_call_id,
                  action: "complete",
                  result: JSON.parse(results[item.tool_call_id]),
                });
              }
            }
            const body: Feedback = {
              kind: "feedback",
              waiting_run_id: runId,
              answers,
            };
            const submitted = data(
              await client.http.POST(
                "/api/v1/workspaces/{workspace_id}/threads/{thread_id}/inbox",
                {
                  params: { path: { ...path, thread_id: threadId } },
                  headers: { "Idempotency-Key": request.forBody(body) },
                  body,
                },
              ),
            );
            setSent(submitted.entry.status !== "failed");
            if (submitted.entry.status === "failed") request.reset();
            onSubmitted(submitted);
          } catch (failure) {
            setError(failure);
          } finally {
            setPending(false);
          }
        }}
      >
        {run.pending.map((item) => (
          <article className={styles.waitingRequest} key={item.tool_call_id}>
            <div className={styles.messageHeading}>
              {t(
                item.kind === "approval"
                  ? "Approval"
                  : item.kind === "client_tool"
                    ? "Client result"
                    : "Question",
              )}
            </div>
            {item.kind === "user_input" ? (
              <>
                {(Array.isArray(item.arguments.questions)
                  ? item.arguments.questions
                  : []
                ).map(
                  (question, index) =>
                    isRecord(question) && (
                      <div key={index}>
                        <h3>{String(question.question ?? "")}</h3>
                        {Array.isArray(question.options) && (
                          <ul>
                            {question.options.map(
                              (option, index) =>
                                isRecord(option) && (
                                  <li key={index}>
                                    <strong>
                                      {String(option.label ?? "")}
                                    </strong>{" "}
                                    — {String(option.description ?? "")}
                                  </li>
                                ),
                            )}
                          </ul>
                        )}
                      </div>
                    ),
                )}
                {control && (
                  <p>
                    {t(
                      "This control response leaves the question unanswered. You can add context in a message.",
                    )}
                  </p>
                )}
              </>
            ) : (
              <>
                <h3>{item.tool_name}</h3>
                <details>
                  <summary>{t("Request details")}</summary>
                  <pre>{JSON.stringify(item.arguments, null, 2)}</pre>
                </details>
                {item.kind === "approval" ? (
                  <label className={styles.approval}>
                    <input
                      type="checkbox"
                      checked={approved[item.tool_call_id] ?? false}
                      disabled={disabled}
                      onChange={(event) =>
                        setApproved({
                          ...approved,
                          [item.tool_call_id]: event.target.checked,
                        })
                      }
                    />
                    {t("Approve this request")}
                  </label>
                ) : (
                  <FormField
                    label={t("Manual JSON result")}
                    description={t(
                      "Enter the result from your client. The Console does not execute this tool.",
                    )}
                  >
                    <Textarea
                      rows={4}
                      maxLength={262144}
                      disabled={disabled}
                      value={results[item.tool_call_id] ?? ""}
                      placeholder={'{"result": "…"}'}
                      onChange={(event) => {
                        const value = event.target.value;
                        try {
                          if (value.trim()) JSON.parse(value);
                          event.currentTarget.setCustomValidity("");
                        } catch {
                          event.currentTarget.setCustomValidity(
                            t("Enter valid JSON or leave the result empty."),
                          );
                        }
                        setResults({ ...results, [item.tool_call_id]: value });
                      }}
                    />
                  </FormField>
                )}
              </>
            )}
          </article>
        ))}
        {control && (
          <Button type="submit" disabled={disabled}>
            {t(pending ? "Submitting…" : "Submit response")}
          </Button>
        )}
      </form>
    </section>
  );
}
