import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button, ChoiceField, Textarea } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useScope } from "../../layout/workspace";
import { data, type components } from "../../service-client";
import { useIdempotency } from "../../shared/idempotency";
import { ErrorNotice } from "../../shared/feedback";
import { Pagination, useCursor } from "../../shared/collection";
import styles from "./conversations.module.css";

type Submitted = components["schemas"]["Submitted"];
export function Composer({
  threadId,
  sessionId,
  currentRunId,
  onSubmitted,
  onInterrupted,
}: {
  threadId?: string;
  sessionId?: string;
  currentRunId?: string | null;
  onSubmitted: (result: Submitted) => void;
  onInterrupted: () => void;
}) {
  const { client, path, cache, workspace } = useScope();
  const { t } = useTranslation();
  const page = useCursor();
  const agents = useQuery({
    queryKey: [...cache, "agents", page.cursor],
    queryFn: async ({ signal }) =>
      data(
        await client.http.GET("/api/v1/workspaces/{workspace_id}/agents", {
          params: { path, query: { limit: 50, cursor: page.cursor } },
          signal,
        }),
      ),
  });
  const [agent, setAgent] = useState<components["schemas"]["AgentView"]>();
  const [text, setText] = useState("");
  const [delivery, setDelivery] = useState<"steer" | "next_run">("steer");
  const [error, setError] = useState<unknown>(null);
  const [pending, setPending] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [stoppedRun, setStoppedRun] = useState<string>();
  const composing = useRef(false);
  const request = useIdempotency();
  const selectedAgent = agent?.id || agents.data?.items[0]?.id || "";
  const agentOptions = agents.data?.items ?? [];
  const visibleAgents =
    agent && !agentOptions.some((item) => item.id === agent.id)
      ? [agent, ...agentOptions]
      : agentOptions;
  const canRun = workspace.permissions.includes("run");
  async function interrupt() {
    if (!currentRunId || stopping || !canRun) return;
    setStopping(true);
    setError(null);
    try {
      await client.http.POST(
        "/api/v1/workspaces/{workspace_id}/runs/{run_id}/interrupt",
        { params: { path: { ...path, run_id: currentRunId } } },
      );
      setStoppedRun(currentRunId);
      onInterrupted();
    } catch (failure) {
      setError(failure);
    } finally {
      setStopping(false);
    }
  }
  if (!canRun)
    return (
      <p className={styles.composer}>
        {t("View only. A runner role is required to send messages.")}
      </p>
    );
  return (
    <form
      className={styles.composer}
      onSubmit={async (event) => {
        event.preventDefault();
        if (pending || !text.trim() || !selectedAgent) return;
        const body = {
          kind: "message" as const,
          delivery,
          agent_id: selectedAgent,
          payload: { content: [{ type: "text" as const, text }] },
        };
        const newBody = { ...body, session_id: sessionId ?? null };
        const key = request.forBody({
          threadId,
          body: threadId ? body : newBody,
        });
        setPending(true);
        setError(null);
        try {
          const result = threadId
            ? data(
                await client.http.POST(
                  "/api/v1/workspaces/{workspace_id}/threads/{thread_id}/inbox",
                  {
                    params: { path: { ...path, thread_id: threadId } },
                    headers: { "Idempotency-Key": key },
                    body,
                  },
                ),
              )
            : data(
                await client.http.POST(
                  "/api/v1/workspaces/{workspace_id}/threads",
                  {
                    params: { path },
                    headers: { "Idempotency-Key": key },
                    body: newBody,
                  },
                ),
              );
          request.reset();
          setText("");
          onSubmitted(result);
        } catch (failure) {
          setError(failure);
        } finally {
          setPending(false);
        }
      }}
    >
      <div className={styles.composerOptions}>
        <ChoiceField
          label={t("Agent")}
          value={selectedAgent}
          options={
            visibleAgents.map((item) => ({
              value: item.id,
              label: item.name,
            })) ?? []
          }
          onValueChange={(id) =>
            setAgent(visibleAgents.find((item) => item.id === id))
          }
          disabled={pending}
        />
        {currentRunId && (
          <ChoiceField
            label={t("Delivery")}
            value={delivery}
            options={[
              { value: "steer", label: t("Steer current run") },
              { value: "next_run", label: t("Queue for next run") },
            ]}
            onValueChange={(value) => {
              if (value === "steer" || value === "next_run") setDelivery(value);
            }}
            disabled={pending}
          />
        )}
      </div>
      <Pagination page={page} next={agents.data?.next_cursor} />
      <Textarea
        aria-label={t("Message")}
        placeholder={t("Message your agent…")}
        rows={3}
        maxLength={65536}
        value={text}
        disabled={pending}
        onChange={(event) => setText(event.target.value)}
        onCompositionStart={() => {
          composing.current = true;
        }}
        onCompositionEnd={() => {
          composing.current = false;
        }}
        onKeyDown={(event) => {
          if (
            event.key === "Enter" &&
            !event.shiftKey &&
            !event.nativeEvent.isComposing &&
            !composing.current &&
            event.keyCode !== 229
          ) {
            event.preventDefault();
            event.currentTarget.form?.requestSubmit();
          }
          if (event.key === "Escape" && !text && currentRunId) {
            event.preventDefault();
            void interrupt();
          }
        }}
      />
      <ErrorNotice error={error ?? agents.error} />
      <div className={styles.composerActions}>
        <small>{t("Enter to send · Shift+Enter for a new line")}</small>
        {currentRunId && (
          <Button
            type="button"
            variant="outline"
            disabled={stopping || stoppedRun === currentRunId}
            onClick={interrupt}
          >
            {t(
              stoppedRun === currentRunId
                ? "Stop requested"
                : stopping
                  ? "Stopping…"
                  : "Stop run",
            )}
          </Button>
        )}
        <Button
          type="submit"
          disabled={pending || !text.trim() || !selectedAgent}
          loading={pending}
        >
          {t("Send")}
        </Button>
      </div>
    </form>
  );
}
