import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data } from "../../shared/api";
import { Avatar, AvatarFallback, AvatarImage } from "a13n-ui";
import { avatarColor, nameInitials } from "../../shared/identity";

export function AgentAvatar({
  name,
  id,
  url,
  className,
}: {
  name: string;
  id?: string;
  url?: string | null;
  className?: string;
}) {
  return (
    <Avatar className={className} aria-hidden="true">
      {url &&
        (url.startsWith("/api/v1/agents/") && id ? (
          <StoredAvatar id={id} versionUrl={url} />
        ) : (
          <AvatarImage src={url} alt="" />
        ))}
      <AvatarFallback
        className="rounded-[inherit] font-semibold text-white"
        style={{ backgroundColor: avatarColor(id ?? name) }}
      >
        {nameInitials(name, 1) || "A"}
      </AvatarFallback>
    </Avatar>
  );
}

/** Authenticated image reads carry the same workspace boundary as every resource read. */
function StoredAvatar({ id, versionUrl }: { id: string; versionUrl: string }) {
  const client = useClient(),
    { workspace } = useWorkspace();
  const image = useQuery({
    queryKey: ["agent-avatar", workspace.id, versionUrl],
    staleTime: Infinity,
    queryFn: async ({ signal }) =>
      data(
        await client
          .workspace(workspace.id)
          .GET("/api/v1/agents/{agent_reference}/avatar", {
            params: { path: { agent_reference: id } },
            parseAs: "blob",
            signal,
          }),
      ),
  });
  const [source, setSource] = useState<string>();
  useEffect(() => {
    if (!image.data) {
      setSource(undefined);
      return;
    }
    const url = URL.createObjectURL(image.data);
    setSource(url);
    return () => URL.revokeObjectURL(url);
  }, [image.data]);
  return source ? <AvatarImage src={source} alt="" /> : null;
}
