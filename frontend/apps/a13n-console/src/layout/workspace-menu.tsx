import { useQueryClient } from "@tanstack/react-query";
import {
  Button,
  Menu,
  MenuGroup,
  MenuGroupLabel,
  MenuItem,
  MenuPopup,
  MenuSeparator,
  MenuTrigger,
} from "a13n-ui";
import { CaretUpDownIcon, CheckIcon, GearSixIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { UserAvatar } from "./avatar";
import { workspacePath } from "../shared/paths";
import { useScope } from "./workspace";
import { useWorkspaces } from "./landing";
import { ErrorNotice } from "../shared/feedback";
import { Pagination } from "../shared/collection";
import styles from "./layout.module.css";

export function WorkspaceMenu({
  onNavigate,
  compact = false,
}: {
  onNavigate: () => void;
  compact?: boolean;
}) {
  const { t } = useTranslation(),
    context = useScope(),
    navigate = useNavigate(),
    cache = useQueryClient();
  const { query, page } = useWorkspaces();
  const workspaces = query.data?.items ?? [];
  const choices = workspaces.some((item) => item.id === context.workspace.id)
    ? workspaces
    : [context.workspace, ...workspaces];
  const open = (path: string) => {
    onNavigate();
    navigate(path);
  };
  return (
    <Menu>
      <MenuTrigger
        openOnHover
        delay={100}
        closeDelay={150}
        aria-label={t("Workspace menu")}
        render={
          compact ? (
            <Button variant="ghost" size="icon-sm" className="mx-auto" />
          ) : (
            <Button variant="ghost" className="w-full justify-start" />
          )
        }
      >
        <UserAvatar
          name={context.workspace.name}
          id={context.workspace.id}
          className="size-5 rounded-md text-[10px]"
        />
        {compact ? (
          <span className="sr-only">{context.workspace.name}</span>
        ) : (
          <>
            <span className="min-w-0 flex-1 truncate text-left">
              {context.workspace.name}
            </span>
            <CaretUpDownIcon aria-hidden="true" />
          </>
        )}
      </MenuTrigger>
      <MenuPopup
        align="start"
        side={compact ? "right" : "bottom"}
        className={compact ? "min-w-56" : "w-(--anchor-width) min-w-56"}
      >
        <MenuGroup>
          <MenuGroupLabel>{t("Workspaces")}</MenuGroupLabel>
          {choices.map((item) => {
            const current = item.id === context.workspace.id;
            return (
              <MenuItem
                key={item.id}
                aria-current={current ? "true" : undefined}
                onClick={() => {
                  if (!current) {
                    void cache.cancelQueries();
                    cache.removeQueries({
                      predicate: (query) =>
                        query.queryKey.includes(context.workspace.id),
                    });
                    navigate(`${workspacePath(item)}/agents`);
                  }
                  onNavigate();
                }}
              >
                <span aria-hidden="true">
                  <UserAvatar
                    name={item.name}
                    id={item.id}
                    className="size-5 rounded-md text-[10px]"
                  />
                </span>
                <span className={styles.menuName}>{item.name}</span>
                {current && (
                  <CheckIcon aria-hidden="true" className="ms-auto" />
                )}
              </MenuItem>
            );
          })}
        </MenuGroup>
        <Pagination page={page} next={query.data?.next_cursor} />
        <ErrorNotice error={query.error} />
        <MenuSeparator />
        <MenuGroup>
          <MenuItem onClick={() => open(`${context.base}/settings`)}>
            <GearSixIcon aria-hidden="true" />
            {t("Workspace settings")}
          </MenuItem>
        </MenuGroup>
      </MenuPopup>
    </Menu>
  );
}
