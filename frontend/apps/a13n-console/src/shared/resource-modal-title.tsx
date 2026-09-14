import type { ReactNode } from "react";
import { ResourceReference } from "./resource-reference";

export function ResourceModalTitle({
  name,
  id,
  labels,
  referenceDetails,
}: {
  name: string;
  id: string;
  labels?: Record<string, string>;
  referenceDetails?: ReactNode;
}) {
  return (
    <span className="flex min-w-0 items-center gap-2">
      <span className="min-w-0 truncate" title={name}>
        {name}
      </span>
      <ResourceReference id={id} labels={labels}>
        {referenceDetails}
      </ResourceReference>
    </span>
  );
}
