import { useEffect, useRef, useState } from "react";
import { ArrowsOut } from "@phosphor-icons/react";
import { useTransport } from "../transport/context";
import { ImagePreview } from "../shell/image-preview";
import { ErrorNotice } from "../shell/ui";
import { basename } from "./buffer";
import type { MediaKind } from "./media-kind";
import { fileTransfer } from "./file-transfer";
import styles from "./file-image.module.css";

/** The parent keys this view by requested path, reviewed revision and refresh. */
export function FileMedia({
  path,
  revision,
  retry,
  kind,
}: {
  path: string;
  revision: string;
  retry: () => void;
  kind: MediaKind;
}) {
  const transport = useTransport();
  const [src, setSrc] = useState<string>();
  const [error, setError] = useState<unknown>();
  const [dimensions, setDimensions] = useState<string>();
  const [expanded, setExpanded] = useState(false);
  const [ready, setReady] = useState(false);
  const player = useRef<HTMLMediaElement>(null);
  const lifetime = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    let url: string | undefined;
    const query = new URLSearchParams({ path, expected_revision: revision });
    const content =
      kind === "image"
        ? transport
            .fetch(`/api/host/files/content?${query}`, {
              signal: controller.signal,
            })
            .then((response) => response.blob())
            .then((blob) => {
              if (controller.signal.aborted) return undefined;
              url = URL.createObjectURL(blob);
              return url;
            })
        : fileTransfer(
            transport,
            path,
            revision,
            "media",
            controller.signal,
          ).then((access) => access.url);
    void content
      .then((source) => {
        if (!controller.signal.aborted) setSrc(source);
      })
      .catch((failure: unknown) => {
        if (!controller.signal.aborted) setError(failure);
      });
    return () => {
      controller.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [transport, path, revision, kind]);
  useEffect(() => {
    const element = player.current;
    return () => {
      if (element) {
        element.pause();
        element.removeAttribute("src");
        element.load();
      }
    };
  }, [src, error]);
  const name = basename(path);
  const label = `${kind[0].toUpperCase()}${kind.slice(1)}`;
  const decodeError = () => {
    const failure = new Error(
      `This ${kind} cannot be previewed. The browser may not support its codec. Download the original to inspect it.`,
    );
    if (kind === "image" || !src) {
      setError(failure);
      return;
    }
    // Native players hide HTTP failures. Distinguish stale/expired access from
    // unsupported codecs without downloading the body or retrying playback.
    const signal = lifetime.current?.signal;
    void transport.fetch(src, { method: "HEAD", signal }).then(
      () => {
        if (!signal?.aborted) setError(failure);
      },
      (error: unknown) => {
        if (!signal?.aborted) setError(error);
      },
    );
  };
  return (
    <section className={styles.preview} aria-label={`${label} preview`}>
      {(!src || (kind === "image" ? !dimensions : !ready)) && !error && (
        <p role="status">Loading {kind}…</p>
      )}
      <ErrorNotice error={error} retry={retry} />
      {src && !error && (
        <>
          {kind === "image" ? (
            <button
              type="button"
              className={styles.canvas}
              aria-label={`Expand image: ${name}`}
              disabled={!dimensions}
              onClick={() => setExpanded(true)}
            >
              <img
                src={src}
                alt={name}
                hidden={!dimensions}
                onLoad={(event) => {
                  const image = event.currentTarget;
                  setDimensions(
                    `${image.naturalWidth} × ${image.naturalHeight}`,
                  );
                }}
                onError={decodeError}
              />
            </button>
          ) : kind === "audio" ? (
            <audio
              ref={(element) => {
                player.current = element;
              }}
              src={src}
              controls
              preload="metadata"
              aria-label={`Audio preview: ${name}`}
              onError={decodeError}
              onLoadedMetadata={() => setReady(true)}
            />
          ) : (
            <video
              ref={(element) => {
                player.current = element;
              }}
              src={src}
              controls
              preload="metadata"
              playsInline
              aria-label={`Video preview: ${name}`}
              onError={decodeError}
              onLoadedMetadata={() => setReady(true)}
            />
          )}
          {dimensions && (
            <div className={styles.caption}>
              <span>{dimensions}</span>
              <span>
                <ArrowsOut aria-hidden="true" /> Click image to expand
              </span>
            </div>
          )}
          {expanded && (
            <ImagePreview
              src={src}
              name={name}
              close={() => setExpanded(false)}
            />
          )}
        </>
      )}
    </section>
  );
}
