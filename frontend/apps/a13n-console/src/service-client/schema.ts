export interface paths {
  "/api/v1/auth/login": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Password Login */
    post: operations["password_login_api_v1_auth_login_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/auth/logout": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Session Logout */
    post: operations["session_logout_api_v1_auth_logout_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/auth/session": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Session Profile */
    get: operations["session_profile_api_v1_auth_session_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/oauth/callback": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Oauth Callback */
    get: operations["oauth_callback_api_v1_oauth_callback_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization_id}/model-providers": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Providers */
    get: operations["list_providers_api_v1_organizations__organization_id__model_providers_get"];
    put?: never;
    /** Create Provider */
    post: operations["create_provider_api_v1_organizations__organization_id__model_providers_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization_id}/model-providers/{provider_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Provider */
    get: operations["get_provider_api_v1_organizations__organization_id__model_providers__provider_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization_id}/models": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Models */
    get: operations["list_models_api_v1_organizations__organization_id__models_get"];
    put?: never;
    /** Create Model */
    post: operations["create_model_api_v1_organizations__organization_id__models_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization_id}/models/{model_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Model */
    get: operations["get_model_api_v1_organizations__organization_id__models__model_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/provider-types/model": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Provider Types */
    get: operations["provider_types_api_v1_provider_types_model_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/users/me": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Me */
    get: operations["me_api_v1_users_me_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/users/me/keys": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Create Key */
    post: operations["create_key_api_v1_users_me_keys_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Workspaces */
    get: operations["list_workspaces_api_v1_workspaces_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Workspace */
    get: operations["get_workspace_api_v1_workspaces__workspace_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/agents": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Agents */
    get: operations["list_agents_api_v1_workspaces__workspace_id__agents_get"];
    put?: never;
    /** Create Agent */
    post: operations["create_agent_api_v1_workspaces__workspace_id__agents_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/agents/{agent_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Agent */
    get: operations["get_agent_api_v1_workspaces__workspace_id__agents__agent_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/agents/{agent_id}/revisions": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Revisions */
    get: operations["list_revisions_api_v1_workspaces__workspace_id__agents__agent_id__revisions_get"];
    put?: never;
    /** Create Revision */
    post: operations["create_revision_api_v1_workspaces__workspace_id__agents__agent_id__revisions_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/agents/{agent_id}/revisions/{revision_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Revision */
    get: operations["get_revision_api_v1_workspaces__workspace_id__agents__agent_id__revisions__revision_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/agents/{agent_id}/revisions/{revision_id}/set-default": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Set Default */
    post: operations["set_default_api_v1_workspaces__workspace_id__agents__agent_id__revisions__revision_id__set_default_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/assets": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Assets */
    get: operations["list_assets_api_v1_workspaces__workspace_id__assets_get"];
    put?: never;
    /** Create Asset */
    post: operations["create_asset_api_v1_workspaces__workspace_id__assets_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/assets/{asset_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Asset */
    get: operations["get_asset_api_v1_workspaces__workspace_id__assets__asset_id__get"];
    put?: never;
    post?: never;
    /** Retire Asset */
    delete: operations["retire_asset_api_v1_workspaces__workspace_id__assets__asset_id__delete"];
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/assets/{asset_id}/content": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Asset Content */
    get: operations["asset_content_api_v1_workspaces__workspace_id__assets__asset_id__content_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/audit-events": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Audit Events */
    get: operations["audit_events_api_v1_workspaces__workspace_id__audit_events_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/connection-catalog/composio": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Composio Catalog */
    post: operations["composio_catalog_api_v1_workspaces__workspace_id__connection_catalog_composio_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/connections": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Connections */
    get: operations["list_connections_api_v1_workspaces__workspace_id__connections_get"];
    put?: never;
    /** Create Connection */
    post: operations["create_connection_api_v1_workspaces__workspace_id__connections_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/connections/{connection_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Connection */
    get: operations["get_connection_api_v1_workspaces__workspace_id__connections__connection_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Connection */
    patch: operations["update_connection_api_v1_workspaces__workspace_id__connections__connection_id__patch"];
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/connections/{connection_id}/authorization": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Authorization Status */
    get: operations["authorization_status_api_v1_workspaces__workspace_id__connections__connection_id__authorization_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/connections/{connection_id}/authorization/complete": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Complete Managed Authorization */
    post: operations["complete_managed_authorization_api_v1_workspaces__workspace_id__connections__connection_id__authorization_complete_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/connections/{connection_id}/authorize": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Authorize Connection */
    post: operations["authorize_connection_api_v1_workspaces__workspace_id__connections__connection_id__authorize_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/connections/{connection_id}/revoke": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Revoke Authorization */
    post: operations["revoke_authorization_api_v1_workspaces__workspace_id__connections__connection_id__revoke_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/connections/{connection_id}/test": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Test Connection */
    post: operations["test_connection_api_v1_workspaces__workspace_id__connections__connection_id__test_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/connections/{connection_id}/tools": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Connection Tools */
    get: operations["connection_tools_api_v1_workspaces__workspace_id__connections__connection_id__tools_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/runs/{run_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Run */
    get: operations["get_run_api_v1_workspaces__workspace_id__runs__run_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/runs/{run_id}/events": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Events */
    get: operations["get_events_api_v1_workspaces__workspace_id__runs__run_id__events_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/runs/{run_id}/interrupt": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Interrupt Run */
    post: operations["interrupt_run_api_v1_workspaces__workspace_id__runs__run_id__interrupt_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/runs/{run_id}/items": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Items */
    get: operations["get_items_api_v1_workspaces__workspace_id__runs__run_id__items_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/sessions": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Sessions */
    get: operations["list_sessions_api_v1_workspaces__workspace_id__sessions_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/sessions/{identity}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Session */
    get: operations["get_session_api_v1_workspaces__workspace_id__sessions__identity__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/threads": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Threads */
    get: operations["list_threads_api_v1_workspaces__workspace_id__threads_get"];
    put?: never;
    /** Create Thread */
    post: operations["create_thread_api_v1_workspaces__workspace_id__threads_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/threads/{identity}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Thread */
    get: operations["get_thread_api_v1_workspaces__workspace_id__threads__identity__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/threads/{thread_id}/inbox": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Inbox */
    get: operations["get_inbox_api_v1_workspaces__workspace_id__threads__thread_id__inbox_get"];
    put?: never;
    /** Append Input */
    post: operations["append_input_api_v1_workspaces__workspace_id__threads__thread_id__inbox_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/threads/{thread_id}/runs": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Runs */
    get: operations["list_runs_api_v1_workspaces__workspace_id__threads__thread_id__runs_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/uploads": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Upload */
    post: operations["upload_api_v1_workspaces__workspace_id__uploads_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/usage": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Usage */
    get: operations["get_usage_api_v1_workspaces__workspace_id__usage_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/healthz": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Health */
    get: operations["health_healthz_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/readyz": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Ready */
    get: operations["ready_readyz_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
}
export type webhooks = Record<string, never>;
export interface components {
  schemas: {
    /** AgentConfig */
    AgentConfig: {
      /**
       * Client Tools
       * @default []
       */
      client_tools?: components["schemas"]["ClientToolDefinition"][];
      /** Compaction Trigger Tokens */
      compaction_trigger_tokens?: number | null;
      /**
       * Connections
       * @default []
       */
      connections?: components["schemas"]["ConnectionSelection"][];
      /**
       * Instructions
       * @default
       */
      instructions?: string;
      /**
       * Max Requests
       * @default 100
       */
      max_requests?: number;
      /** Model Id */
      model_id: string;
      tool_permissions?: components["schemas"]["ToolPermissions"];
      /**
       * User Questions
       * @default false
       */
      user_questions?: boolean;
    };
    /** AgentCreate */
    AgentCreate: {
      config: components["schemas"]["AgentConfig"];
      /**
       * Description
       * @default
       */
      description?: string;
      /** Key */
      key: string;
      /** Labels */
      labels?: {
        [key: string]: string;
      };
      /** Name */
      name: string;
    };
    /** AgentPage */
    AgentPage: {
      /** Items */
      items: components["schemas"]["AgentView"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** AgentView */
    AgentView: {
      /** Default Revision Id */
      default_revision_id: string | null;
      /** Description */
      description: string;
      /** Id */
      id: string;
      /** Key */
      key: string;
      /** Labels */
      labels: {
        [key: string]: string;
      };
      /** Name */
      name: string;
      /** Organization Id */
      organization_id: string;
      /** Source */
      source: string;
      /** Version */
      version: number;
      /** Workspace Id */
      workspace_id: string;
    };
    Answer:
      | components["schemas"]["Approve"]
      | components["schemas"]["Reject"]
      | components["schemas"]["Complete"];
    /** Approve */
    Approve: {
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      action: "approve";
      /** Tool Call Id */
      tool_call_id: string;
    };
    /** AssetCreate */
    AssetCreate: {
      /** Name */
      name: string;
      /** Upload Id */
      upload_id: string;
    };
    /** AssetInput */
    AssetInput: {
      /** Asset Id */
      asset_id: string;
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      type: "asset";
    };
    /** AssetPage */
    AssetPage: {
      /** Items */
      items: components["schemas"]["AssetView"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** AssetView */
    AssetView: {
      /** Content Type */
      content_type: string;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Digest */
      digest: string;
      /** Id */
      id: string;
      /** Name */
      name: string;
      /** Retired At */
      retired_at: string | null;
      /** Size */
      size: number;
      /** Source */
      source: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      /** Version */
      version: number;
      /** Workspace Id */
      workspace_id: string;
    };
    /** AuditEvent */
    AuditEvent: {
      /** Action */
      action: string;
      /** Actor Id */
      actor_id: string | null;
      /** Details */
      details: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Id */
      id: string;
      /**
       * Occurred At
       * Format: date-time
       */
      occurred_at: string;
      /** Organization Id */
      organization_id: string;
      /** Outcome */
      outcome: string;
      /** Target Id */
      target_id: string;
      /** Target Kind */
      target_kind: string;
      /** Workspace Id */
      workspace_id: string;
    };
    /** AuditPage */
    AuditPage: {
      /** Items */
      items: components["schemas"]["AuditEvent"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** Authentication */
    Authentication: {
      /**
       * Cases
       * @default []
       */
      cases?: components["schemas"]["AuthenticationCase"][];
      /** @default required */
      mode?: components["schemas"]["CredentialMode"];
    };
    /**
     * AuthenticationCase
     * @description Override credential presence for one declared configuration field value.
     */
    AuthenticationCase: {
      /** Equals */
      equals: string | number | boolean | null;
      /** Field */
      field: string;
      mode: components["schemas"]["CredentialMode"];
    };
    /** AuthorizationStart */
    AuthorizationStart: {
      authorization: components["schemas"]["AuthorizationView"];
      /** Redirect Url */
      redirect_url: string;
    };
    /** AuthorizationView */
    AuthorizationView: {
      /** Connection Id */
      connection_id: string;
      /** Expires At */
      expires_at: string | null;
      /** Failure */
      failure: {
        [key: string]: string;
      } | null;
      /** Generation */
      generation: number;
      /** Id */
      id: string | null;
      /** Operation Kind */
      operation_kind:
        ("exchange" | "refresh" | "setup" | "complete" | "revoke") | null;
      /**
       * Status
       * @enum {string}
       */
      status:
        | "not_authorized"
        | "pending"
        | "active"
        | "revoked"
        | "reauthorization_required";
    };
    /** AuthorizeRequest */
    AuthorizeRequest: {
      /** Return Url */
      return_url: string;
    };
    /** BearerCredential */
    BearerCredential: {
      /**
       * Token
       * Format: password
       */
      token: string;
    };
    /** Body_upload_api_v1_workspaces__workspace_id__uploads_post */
    Body_upload_api_v1_workspaces__workspace_id__uploads_post: {
      /** File */
      file: string;
    };
    /** CatalogRequest */
    CatalogRequest: {
      /** App */
      app?: string | null;
      /** Connection Id */
      connection_id?: string | null;
      credential?: components["schemas"]["ManagedCredential"] | null;
      /** Toolkit Version */
      toolkit_version?: string | null;
    };
    /** CatalogView */
    CatalogView: {
      /** Apps */
      apps: components["schemas"]["DiscoveredConnector"][];
      /** Toolkit Version */
      toolkit_version: string | null;
      /** Tools */
      tools: components["schemas"]["ToolInfo"][];
    };
    /**
     * ClientToolDefinition
     * @description Portable model guidance for one externally executed client tool.
     */
    ClientToolDefinition: {
      /** Description */
      description: string;
      /** Instruction */
      instruction?: string | null;
      /** Metadata */
      metadata?: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Name */
      name: string;
      /** Parameters Json Schema */
      parameters_json_schema: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /**
       * Permission
       * @default inherit
       * @enum {string}
       */
      permission?: "inherit" | "allow" | "deny";
    };
    /** Complete */
    Complete: {
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      action: "complete";
      result: components["schemas"]["JsonValue"];
      /** Tool Call Id */
      tool_call_id: string;
    };
    /** ComposioConfig */
    ComposioConfig: {
      /** Actions */
      actions: string[];
      /** App */
      app: string;
      /** Auth Config Id */
      auth_config_id: string;
      connection_data?: components["schemas"]["JsonObject"];
      /** Toolkit Version */
      toolkit_version: string;
    };
    /** @enum {string} */
    ConnectionAuthentication:
      "none" | "bearer" | "headers" | "oauth" | "managed";
    ConnectionConfig:
      | components["schemas"]["MCPConfig"]
      | components["schemas"]["ComposioConfig"];
    /** ConnectionCreate */
    ConnectionCreate: {
      /** @default none */
      auth?: components["schemas"]["ConnectionAuthentication"];
      config: components["schemas"]["ConnectionConfig"];
      credential?: components["schemas"]["Credential"] | null;
      /** Name */
      name: string;
      /**
       * Type
       * @enum {string}
       */
      type: "mcp" | "composio";
    };
    /** ConnectionPage */
    ConnectionPage: {
      /** Items */
      items: components["schemas"]["ConnectionView"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** ConnectionSelection */
    ConnectionSelection: {
      /** Connection Id */
      connection_id: string;
      /** Tools */
      tools: string[];
    };
    /** ConnectionTest */
    ConnectionTest: {
      /** Connection Id */
      connection_id: string;
      /** Tools */
      tools: components["schemas"]["ToolInfo"][];
      /** Version */
      version: number;
    };
    /** ConnectionUpdate */
    ConnectionUpdate: {
      auth?: components["schemas"]["ConnectionAuthentication"] | null;
      config?: components["schemas"]["ConnectionConfig"] | null;
      credential?: components["schemas"]["Credential"] | null;
      /** Enabled */
      enabled?: boolean | null;
      /** Name */
      name?: string | null;
    };
    /** ConnectionView */
    ConnectionView: {
      auth: components["schemas"]["ConnectionAuthentication"];
      config: components["schemas"]["ConnectionConfig"];
      /** Credential Configured */
      credential_configured: boolean;
      /** Enabled */
      enabled: boolean;
      /** Id */
      id: string;
      /** Name */
      name: string;
      /** Organization Id */
      organization_id: string;
      /** Type */
      type: string;
      /** Version */
      version: number;
      /** Workspace Id */
      workspace_id: string;
    };
    Credential:
      | components["schemas"]["BearerCredential"]
      | components["schemas"]["HeadersCredential"]
      | components["schemas"]["OAuthClientCredential"]
      | components["schemas"]["ManagedCredential"];
    /**
     * CredentialMode
     * @enum {string}
     */
    CredentialMode: "required" | "optional" | "forbidden";
    /** DiscoveredConnector */
    DiscoveredConnector: {
      /** Authentication Methods */
      authentication_methods: string[];
      /** Credential Schemas */
      credential_schemas?: {
        [key: string]: components["schemas"]["JsonObject"];
      };
      /** Description */
      description?: string | null;
      /** Key */
      key: string;
      /** Logo Url */
      logo_url?: string | null;
      /** Name */
      name: string;
      setup_schema: components["schemas"]["JsonObject"];
      /** Unavailable Reason */
      unavailable_reason?: string | null;
    };
    /** DisplayCut */
    DisplayCut: {
      /** Attempt Id */
      attempt_id: string;
      /** Event Sequence */
      event_sequence: number;
      /** Sequence */
      sequence: number;
    };
    /** EntryView */
    EntryView: {
      /** Assigned Run Id */
      assigned_run_id: string | null;
      /** Delivery */
      delivery: string;
      /** Failure */
      failure: {
        [key: string]: unknown;
      } | null;
      /** Id */
      id: string;
      /** Incorporated Checkpoint Seq */
      incorporated_checkpoint_seq: number | null;
      /** Kind */
      kind: string;
      /** Payload */
      payload:
        | components["schemas"]["MessagePayload"]
        | components["schemas"]["FeedbackPayload"]
        | null;
      /** Position */
      position: number;
      /**
       * Status
       * @enum {string}
       */
      status: "pending" | "assigned" | "consumed" | "failed" | "withdrawn";
      /** Thread Id */
      thread_id: string;
      /** Waiting Run Id */
      waiting_run_id?: string | null;
    };
    /** EnvironmentPathInput */
    EnvironmentPathInput: {
      /** Mount */
      mount: string;
      /** Path */
      path: string;
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      type: "environment_path";
    };
    /** FeedbackPayload */
    FeedbackPayload: {
      /** Answers */
      answers: components["schemas"]["NormalizedAnswer"][];
    };
    /** FeedbackSubmission */
    FeedbackSubmission: {
      /**
       * Answers
       * @default []
       */
      answers?: components["schemas"]["Answer"][];
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      kind: "feedback";
      /** Waiting Run Id */
      waiting_run_id: string;
    };
    /** HTTPValidationError */
    HTTPValidationError: {
      /** Detail */
      detail?: components["schemas"]["ValidationError"][];
    };
    /** HeadersCredential */
    HeadersCredential: {
      /** Headers */
      headers: {
        [key: string]: string;
      };
    };
    /** InboxPage */
    InboxPage: {
      /** Items */
      items: components["schemas"]["EntryView"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** JsonInput */
    JsonInput: {
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      type: "json";
      value: components["schemas"]["JsonValue"];
    };
    JsonObject: {
      [key: string]: components["schemas"]["JsonValue"];
    };
    JsonValue: unknown;
    /** KeyInput */
    KeyInput: {
      /** Name */
      name: string;
      /** Workspace Id */
      workspace_id: string;
    };
    /** KeyOutput */
    KeyOutput: {
      /** Id */
      id: string;
      /** Name */
      name: string;
      /** Secret */
      secret: string;
      /** Workspace Id */
      workspace_id: string;
    };
    /** LoginInput */
    LoginInput: {
      /**
       * Email
       * Format: email
       */
      email: string;
      /**
       * Password
       * Format: password
       */
      password: string;
    };
    /** LoginOutput */
    LoginOutput: {
      /** Csrf Token */
      csrf_token: string;
      /** Principal Id */
      principal_id: string;
    };
    /** MCPConfig */
    MCPConfig: {
      oauth?: components["schemas"]["OAuthConfig"] | null;
      /**
       * Recovery Retry Safe Tools
       * @default []
       */
      recovery_retry_safe_tools?: string[];
      /** Tools */
      tools?: string[] | null;
      /** Url */
      url: string;
    };
    /** ManagedCompleted */
    ManagedCompleted: {
      /** Return Url */
      return_url: string;
    };
    /** ManagedCompletion */
    ManagedCompletion: {
      /** Authorization Id */
      authorization_id: string;
      /** Generation */
      generation: number;
      /** Session Uri */
      session_uri?: string | null;
    };
    /** ManagedCredential */
    ManagedCredential: {
      /**
       * Api Key
       * Format: password
       */
      api_key: string;
    };
    /** MessagePayload */
    MessagePayload: {
      /** Content */
      content: (
        | components["schemas"]["TextInput"]
        | components["schemas"]["AssetInput"]
        | components["schemas"]["UrlInput"]
        | components["schemas"]["EnvironmentPathInput"]
        | components["schemas"]["JsonInput"]
      )[];
    };
    /** ModelConfig */
    ModelConfig: {
      /** Context Window */
      context_window: number;
      /** Max Tokens */
      max_tokens?: number | null;
      /**
       * Model Api
       * @enum {string}
       */
      model_api: "openai.responses" | "openai.chat_completions";
      /** Model Name */
      model_name: string;
      /** Temperature */
      temperature?: number | null;
      /** Top P */
      top_p?: number | null;
    };
    /** ModelCreate */
    ModelCreate: {
      config: components["schemas"]["ModelConfig"];
      /** Key */
      key: string;
      /** Name */
      name: string;
      pricing?: components["schemas"]["ModelPricingEntry-Input"] | null;
      /** Provider Id */
      provider_id: string;
      /** Workspace Id */
      workspace_id?: string | null;
    };
    /** ModelPage */
    ModelPage: {
      /** Items */
      items: components["schemas"]["ModelView"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /**
     * ModelPriceRule
     * @description One complete set of prices and the condition selecting it.
     */
    "ModelPriceRule-Input": {
      constraint?: components["schemas"]["PricingConstraint"];
      /** Prices */
      prices: components["schemas"]["PriceComponent-Input"][];
      /** Rule Id */
      rule_id: string;
    };
    /**
     * ModelPriceRule
     * @description One complete set of prices and the condition selecting it.
     */
    "ModelPriceRule-Output": {
      constraint?: components["schemas"]["PricingConstraint"];
      /** Prices */
      prices: components["schemas"]["PriceComponent-Output"][];
      /** Rule Id */
      rule_id: string;
    };
    /**
     * ModelPricingEntry
     * @description Complete pricing declaration for one provider-qualified model.
     */
    "ModelPricingEntry-Input": {
      /** Context Window */
      context_window?: number | null;
      /** Model */
      model: string;
      /** Provider */
      provider: string;
      /** Rules */
      rules: components["schemas"]["ModelPriceRule-Input"][];
      /** Source */
      source: string;
      /** Source Revision */
      source_revision: string;
      /** Source Url */
      source_url?: string | null;
    };
    /**
     * ModelPricingEntry
     * @description Complete pricing declaration for one provider-qualified model.
     */
    "ModelPricingEntry-Output": {
      /** Context Window */
      context_window?: number | null;
      /** Model */
      model: string;
      /** Provider */
      provider: string;
      /** Rules */
      rules: components["schemas"]["ModelPriceRule-Output"][];
      /** Source */
      source: string;
      /** Source Revision */
      source_revision: string;
      /** Source Url */
      source_url?: string | null;
    };
    /** ModelView */
    ModelView: {
      config: components["schemas"]["ModelConfig"];
      /** Enabled */
      enabled: boolean;
      /** Id */
      id: string;
      /** Key */
      key: string;
      /** Name */
      name: string;
      /** Organization Id */
      organization_id: string;
      pricing: components["schemas"]["ModelPricingEntry-Output"] | null;
      /** Provider Id */
      provider_id: string;
      /** Version */
      version: number;
      /** Workspace Id */
      workspace_id: string | null;
    };
    /** NewThread */
    NewThread: {
      /** Agent Id */
      agent_id: string;
      /** Agent Revision Id */
      agent_revision_id?: string | null;
      /**
       * Delivery
       * @default steer
       * @enum {string}
       */
      delivery?: "steer" | "next_run";
      /**
       * Kind
       * @constant
       */
      kind: "message";
      options?: components["schemas"]["RunOptions"];
      payload: components["schemas"]["MessagePayload"];
      /** Session Id */
      session_id?: string | null;
    };
    /** NoResponse */
    NoResponse: {
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      action: "no_response";
      /** Tool Call Id */
      tool_call_id: string;
    };
    NormalizedAnswer:
      | components["schemas"]["Approve"]
      | components["schemas"]["Reject"]
      | components["schemas"]["Complete"]
      | components["schemas"]["NoResponse"];
    /** OAuthClientCredential */
    OAuthClientCredential: {
      /**
       * Client Secret
       * Format: password
       */
      client_secret: string;
    };
    /** OAuthConfig */
    OAuthConfig: {
      /** Client Id */
      client_id: string;
      /** Issuer */
      issuer: string;
      /**
       * Scopes
       * @default []
       */
      scopes?: string[];
      /**
       * Token Endpoint Auth Method
       * @default none
       * @enum {string}
       */
      token_endpoint_auth_method?:
        "none" | "client_secret_basic" | "client_secret_post";
    };
    /** PendingItem */
    PendingItem: {
      /** Arguments */
      arguments: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      kind: components["schemas"]["PendingKind"];
      /** Tool Call Id */
      tool_call_id: string;
      /** Tool Name */
      tool_name: string;
    };
    /** @enum {string} */
    PendingKind: "approval" | "client_tool" | "user_input";
    /**
     * PriceComponent
     * @description One genai-prices usage dimension and its USD unit price.
     */
    "PriceComponent-Input": {
      /** Price */
      price: number | string;
      /** Price Key */
      price_key: string;
      /**
       * Tiers
       * @default []
       */
      tiers?: components["schemas"]["PriceTier-Input"][];
    };
    /**
     * PriceComponent
     * @description One genai-prices usage dimension and its USD unit price.
     */
    "PriceComponent-Output": {
      /** Price */
      price: string;
      /** Price Key */
      price_key: string;
      /**
       * Tiers
       * @default []
       */
      tiers?: components["schemas"]["PriceTier-Output"][];
    };
    /**
     * PriceTier
     * @description One cliff-pricing threshold applied to the complete usage quantity.
     */
    "PriceTier-Input": {
      /** Price */
      price: number | string;
      /** Start */
      start: number;
    };
    /**
     * PriceTier
     * @description One cliff-pricing threshold applied to the complete usage quantity.
     */
    "PriceTier-Output": {
      /** Price */
      price: string;
      /** Start */
      start: number;
    };
    /**
     * PricingConstraint
     * @description A stable condition selecting one ordered model price rule.
     */
    PricingConstraint: {
      /** End Time */
      end_time?: string | null;
      /** @default always */
      kind?: components["schemas"]["PricingConstraintKind"];
      /** Start Date */
      start_date?: string | null;
      /** Start Time */
      start_time?: string | null;
      /**
       * Weekdays
       * @default []
       */
      weekdays?: number[];
    };
    /** @enum {string} */
    PricingConstraintKind: "always" | "start_date" | "daily_time";
    /** Profile */
    Profile: {
      /** Email */
      email: string | null;
      /** Id */
      id: string;
      /** Kind */
      kind: string;
      /** Name */
      name: string;
    };
    /** ProviderCreate */
    ProviderCreate: {
      /** Config */
      config: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Credential */
      credential?: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
      /** Name */
      name: string;
      /** Type */
      type: string;
      /** Workspace Id */
      workspace_id?: string | null;
    };
    /** ProviderPage */
    ProviderPage: {
      /** Items */
      items: components["schemas"]["ProviderView"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** ProviderType */
    ProviderType: {
      authentication: components["schemas"]["Authentication"];
      /** Configuration Schema */
      configuration_schema: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Credential Schema */
      credential_schema: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
      /** Display Name */
      display_name: string;
      /** Setup Url */
      setup_url: string | null;
      /** Supported Model Apis */
      supported_model_apis: string[];
      /** Type */
      type: string;
    };
    /** ProviderTypePage */
    ProviderTypePage: {
      /** Items */
      items: components["schemas"]["ProviderType"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** ProviderView */
    ProviderView: {
      /** Config */
      config: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Credential Configured */
      credential_configured: boolean;
      /** Enabled */
      enabled: boolean;
      /** Id */
      id: string;
      /** Name */
      name: string;
      /** Organization Id */
      organization_id: string;
      /** Type */
      type: string;
      /** Version */
      version: number;
      /** Workspace Id */
      workspace_id: string | null;
    };
    /** Reject */
    Reject: {
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      action: "reject";
      /** Reason */
      reason?: string | null;
      /** Tool Call Id */
      tool_call_id: string;
    };
    /** RevisionCreate */
    RevisionCreate: {
      config: components["schemas"]["AgentConfig"];
      /**
       * Make Default
       * @default true
       */
      make_default?: boolean;
      /** Note */
      note?: string | null;
    };
    /** RevisionPage */
    RevisionPage: {
      /** Items */
      items: components["schemas"]["RevisionView"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** RevisionView */
    RevisionView: {
      /** Agent Id */
      agent_id: string;
      config: components["schemas"]["AgentConfig"];
      /** Digest */
      digest: string;
      /** Id */
      id: string;
      /** Note */
      note: string | null;
      /** Number */
      number: number;
    };
    /** RunItems */
    RunItems: {
      /** Complete */
      complete: boolean;
      /** Current Attempt Id */
      current_attempt_id: string | null;
      /** Cursor */
      cursor: string | null;
      /** Display Version */
      display_version: string;
      execution_checkpoint_cut: components["schemas"]["DisplayCut"] | null;
      /** Failure */
      failure: {
        [key: string]: unknown;
      } | null;
      /** Inputs */
      inputs: components["schemas"]["EntryView"][];
      /** Next Input Cursor */
      next_input_cursor: string | null;
      /** Output */
      output: {
        [key: string]: unknown;
      } | null;
      /** Pending */
      pending?: components["schemas"]["PendingItem"][] | null;
      /** Run Id */
      run_id: string;
      /** Segments */
      segments: components["schemas"]["Segment"][];
      status: components["schemas"]["RunStatus"];
      wait_reason?: components["schemas"]["WaitReason"] | null;
      /** Workspace Id */
      workspace_id: string;
    };
    /** RunOptions */
    RunOptions: {
      /** Labels */
      labels?: {
        [key: string]: string;
      };
      max_usage?: components["schemas"]["UsageLimit"] | null;
      /** Mcp Headers */
      mcp_headers?: {
        [key: string]: {
          [key: string]: string;
        };
      };
    };
    /** RunPage */
    RunPage: {
      /** Items */
      items: components["schemas"]["RunView"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** @enum {string} */
    RunStatus:
      "accepted" | "running" | "waiting" | "completed" | "failed" | "cancelled";
    /** @enum {string} */
    RunTrigger: "input" | "queued" | "feedback" | "child_result" | "spawned";
    /** RunView */
    RunView: {
      /** Agent Id */
      agent_id: string;
      /** Agent Revision Id */
      agent_revision_id: string;
      /** Cancel Requested At */
      cancel_requested_at: string | null;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Current Attempt Id */
      current_attempt_id: string | null;
      /** Failure */
      failure: {
        [key: string]: unknown;
      } | null;
      /** Id */
      id: string;
      /** Labels */
      labels: {
        [key: string]: string;
      };
      /** Output */
      output: {
        [key: string]: unknown;
      } | null;
      /** Parent Run Id */
      parent_run_id: string | null;
      /** Pending */
      pending?: components["schemas"]["PendingItem"][] | null;
      /** Sealed At */
      sealed_at: string | null;
      /** Session Id */
      session_id: string;
      /** Source Entry Id */
      source_entry_id: string;
      status: components["schemas"]["RunStatus"];
      /** Thread Id */
      thread_id: string;
      trigger: components["schemas"]["RunTrigger"];
      /** Version */
      version: number;
      wait_reason?: components["schemas"]["WaitReason"] | null;
    };
    /** Segment */
    Segment: {
      /** Attempt Id */
      attempt_id: string;
      /** Attempt Number */
      attempt_number: number;
      /**
       * Event Sequence
       * @default 0
       */
      event_sequence?: number;
      execution_cut?: components["schemas"]["DisplayCut"] | null;
      /**
       * Interrupted
       * @default false
       */
      interrupted?: boolean;
      /** Items */
      items?: {
        [key: string]: components["schemas"]["JsonValue"];
      }[];
    };
    /** SessionPage */
    SessionPage: {
      /** Items */
      items: components["schemas"]["SessionView"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** SessionPreview */
    SessionPreview: {
      /** Agent Id */
      agent_id: string;
      /** Agent Name */
      agent_name: string;
      /** Input Text */
      input_text: string | null;
      /** Run Id */
      run_id: string;
      run_status: components["schemas"]["RunStatus"];
      /** Thread Id */
      thread_id: string;
      trigger: components["schemas"]["RunTrigger"];
    };
    /** SessionProfile */
    SessionProfile: {
      /** Csrf Token */
      csrf_token: string;
      user: components["schemas"]["Profile"];
    };
    /** SessionView */
    SessionView: {
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Id */
      id: string;
      /** Labels */
      labels: {
        [key: string]: string;
      };
      preview: components["schemas"]["SessionPreview"] | null;
      /** Run Count */
      run_count: number;
      /** Selected Thread Id */
      selected_thread_id: string | null;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      /** Version */
      version: number;
      /** Workspace Id */
      workspace_id: string;
    };
    /** Submission */
    Submission: {
      /** Agent Id */
      agent_id: string;
      /** Agent Revision Id */
      agent_revision_id?: string | null;
      /**
       * Delivery
       * @default steer
       * @enum {string}
       */
      delivery?: "steer" | "next_run";
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      kind: "message";
      options?: components["schemas"]["RunOptions"];
      payload: components["schemas"]["MessagePayload"];
    };
    /** Submitted */
    Submitted: {
      entry: components["schemas"]["EntryView"];
      /** Replayed */
      replayed: boolean;
      run: components["schemas"]["RunView"] | null;
      /** Session Id */
      session_id: string;
      /** Thread Id */
      thread_id: string;
    };
    /** TextInput */
    TextInput: {
      /** Text */
      text: string;
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      type: "text";
    };
    /** ThreadPage */
    ThreadPage: {
      /** Items */
      items: components["schemas"]["ThreadView"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** ThreadView */
    ThreadView: {
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Current Run Id */
      current_run_id: string | null;
      /** Head Run Id */
      head_run_id: string | null;
      /** Id */
      id: string;
      /** Labels */
      labels: {
        [key: string]: string;
      };
      /** Last Run Id */
      last_run_id: string | null;
      /** Origin */
      origin: string;
      /** Session Id */
      session_id: string;
      /** Version */
      version: number;
      /** Workspace Id */
      workspace_id: string;
    };
    /** ToolInfo */
    ToolInfo: {
      /** Description */
      description: string | null;
      /** Input Schema */
      input_schema: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Name */
      name: string;
      /** Permission Id */
      permission_id?: string | null;
    };
    /** @enum {string} */
    ToolPermissionMode: "allow" | "deny" | "ask" | "review";
    ToolPermissionSetting:
      components["schemas"]["ToolPermissionMode"] | "inherit";
    /**
     * ToolPermissions
     * @description Portable configuration. Inherit resolves a tool default, never an execution decision.
     */
    ToolPermissions: {
      /** @default inherit */
      default?: components["schemas"]["ToolPermissionSetting"];
      /** Rules */
      rules?: {
        [key: string]: components["schemas"]["ToolPermissionSetting"];
      };
    };
    /** UploadView */
    UploadView: {
      /** Content Type */
      content_type: string;
      /** Digest */
      digest: string;
      /** Filename */
      filename: string;
      /** Size */
      size: number;
      /** Upload Id */
      upload_id: string;
    };
    /** UrlInput */
    UrlInput: {
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      type: "url";
      /** Url */
      url: string;
    };
    /** UsageLimit */
    UsageLimit: {
      /** Requests */
      requests: number;
    };
    /** UsageView */
    UsageView: {
      /** At Seal */
      at_seal: {
        [key: string]: number;
      } | null;
      /** Current */
      current: {
        [key: string]: number;
      };
      /** Run Id */
      run_id: string;
    };
    /** ValidationError */
    ValidationError: {
      /** Context */
      ctx?: Record<string, never>;
      /** Input */
      input?: unknown;
      /** Location */
      loc: (string | number)[];
      /** Message */
      msg: string;
      /** Error Type */
      type: string;
    };
    /** @enum {string} */
    Verb: "read" | "run" | "write" | "admin";
    /** @enum {string} */
    WaitReason: "approval" | "client_tool" | "user_input" | "multiple";
    /** Workspace */
    Workspace: {
      /** Id */
      id: string;
      /** Key */
      key: string;
      /** Name */
      name: string;
      /** Organization Id */
      organization_id: string;
      /** Permissions */
      permissions: components["schemas"]["Verb"][];
      /** Version */
      version: number;
    };
    /** WorkspacePage */
    WorkspacePage: {
      /** Items */
      items: components["schemas"]["Workspace"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
  };
  responses: never;
  parameters: never;
  requestBodies: never;
  headers: never;
  pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
  password_login_api_v1_auth_login_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["LoginInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["LoginOutput"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  session_logout_api_v1_auth_logout_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": {
            [key: string]: boolean;
          };
        };
      };
    };
  };
  session_profile_api_v1_auth_session_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SessionProfile"];
        };
      };
    };
  };
  oauth_callback_api_v1_oauth_callback_get: {
    parameters: {
      query: {
        state: string;
        code?: string | null;
        error?: string | null;
        iss?: string | null;
      };
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": unknown;
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_providers_api_v1_organizations__organization_id__model_providers_get: {
    parameters: {
      query?: {
        workspace_id?: string | null;
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        organization_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ProviderPage"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  create_provider_api_v1_organizations__organization_id__model_providers_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        organization_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ProviderCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ProviderView"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_provider_api_v1_organizations__organization_id__model_providers__provider_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        organization_id: string;
        provider_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ProviderView"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_models_api_v1_organizations__organization_id__models_get: {
    parameters: {
      query?: {
        workspace_id?: string | null;
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        organization_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelPage"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  create_model_api_v1_organizations__organization_id__models_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        organization_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ModelCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelView"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_model_api_v1_organizations__organization_id__models__model_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        organization_id: string;
        model_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelView"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  provider_types_api_v1_provider_types_model_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ProviderTypePage"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  me_api_v1_users_me_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Profile"];
        };
      };
    };
  };
  create_key_api_v1_users_me_keys_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["KeyInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["KeyOutput"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_workspaces_api_v1_workspaces_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["WorkspacePage"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_workspace_api_v1_workspaces__workspace_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Workspace"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_agents_api_v1_workspaces__workspace_id__agents_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        workspace_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AgentPage"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  create_agent_api_v1_workspaces__workspace_id__agents_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["AgentCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AgentView"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_agent_api_v1_workspaces__workspace_id__agents__agent_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace_id: string;
        agent_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AgentView"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_revisions_api_v1_workspaces__workspace_id__agents__agent_id__revisions_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        workspace_id: string;
        agent_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RevisionPage"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  create_revision_api_v1_workspaces__workspace_id__agents__agent_id__revisions_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace_id: string;
        agent_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["RevisionCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RevisionView"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_revision_api_v1_workspaces__workspace_id__agents__agent_id__revisions__revision_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace_id: string;
        agent_id: string;
        revision_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RevisionView"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  set_default_api_v1_workspaces__workspace_id__agents__agent_id__revisions__revision_id__set_default_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace_id: string;
        agent_id: string;
        revision_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AgentView"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_assets_api_v1_workspaces__workspace_id__assets_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        workspace_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AssetPage"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  create_asset_api_v1_workspaces__workspace_id__assets_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["AssetCreate"];
      };
    };
    responses: {
      /** @description Existing Asset with the same upload and name */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AssetView"];
        };
      };
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AssetView"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_asset_api_v1_workspaces__workspace_id__assets__asset_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace_id: string;
        asset_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AssetView"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  retire_asset_api_v1_workspaces__workspace_id__assets__asset_id__delete: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace_id: string;
        asset_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AssetView"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  asset_content_api_v1_workspaces__workspace_id__assets__asset_id__content_get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace_id: string;
        asset_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content?: never;
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  audit_events_api_v1_workspaces__workspace_id__audit_events_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        workspace_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AuditPage"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  composio_catalog_api_v1_workspaces__workspace_id__connection_catalog_composio_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CatalogRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["CatalogView"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_connections_api_v1_workspaces__workspace_id__connections_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        workspace_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectionPage"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  create_connection_api_v1_workspaces__workspace_id__connections_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ConnectionCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectionView"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_connection_api_v1_workspaces__workspace_id__connections__connection_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace_id: string;
        connection_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectionView"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  update_connection_api_v1_workspaces__workspace_id__connections__connection_id__patch: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace_id: string;
        connection_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ConnectionUpdate"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectionView"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  authorization_status_api_v1_workspaces__workspace_id__connections__connection_id__authorization_get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace_id: string;
        connection_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AuthorizationView"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  complete_managed_authorization_api_v1_workspaces__workspace_id__connections__connection_id__authorization_complete_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace_id: string;
        connection_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ManagedCompletion"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ManagedCompleted"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  authorize_connection_api_v1_workspaces__workspace_id__connections__connection_id__authorize_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace_id: string;
        connection_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["AuthorizeRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AuthorizationStart"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  revoke_authorization_api_v1_workspaces__workspace_id__connections__connection_id__revoke_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace_id: string;
        connection_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AuthorizationView"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  test_connection_api_v1_workspaces__workspace_id__connections__connection_id__test_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace_id: string;
        connection_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectionTest"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  connection_tools_api_v1_workspaces__workspace_id__connections__connection_id__tools_get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace_id: string;
        connection_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectionTest"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_run_api_v1_workspaces__workspace_id__runs__run_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace_id: string;
        run_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RunView"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_events_api_v1_workspaces__workspace_id__runs__run_id__events_get: {
    parameters: {
      query?: {
        cursor?: string | null;
      };
      header?: never;
      path: {
        workspace_id: string;
        run_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content?: never;
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  interrupt_run_api_v1_workspaces__workspace_id__runs__run_id__interrupt_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace_id: string;
        run_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RunView"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_items_api_v1_workspaces__workspace_id__runs__run_id__items_get: {
    parameters: {
      query?: {
        limit?: number;
        input_cursor?: string | null;
      };
      header?: never;
      path: {
        workspace_id: string;
        run_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RunItems"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_sessions_api_v1_workspaces__workspace_id__sessions_get: {
    parameters: {
      query?: {
        q?: string | null;
        agent_id?: string | null;
        status?: components["schemas"]["RunStatus"][];
        trigger?: components["schemas"]["RunTrigger"][];
        updated_after?: string | null;
        updated_before?: string | null;
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        workspace_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SessionPage"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_session_api_v1_workspaces__workspace_id__sessions__identity__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace_id: string;
        identity: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SessionView"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_threads_api_v1_workspaces__workspace_id__threads_get: {
    parameters: {
      query?: {
        session_id?: string | null;
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        workspace_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ThreadPage"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  create_thread_api_v1_workspaces__workspace_id__threads_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["NewThread"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Submitted"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_thread_api_v1_workspaces__workspace_id__threads__identity__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace_id: string;
        identity: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ThreadView"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_inbox_api_v1_workspaces__workspace_id__threads__thread_id__inbox_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        workspace_id: string;
        thread_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["InboxPage"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  append_input_api_v1_workspaces__workspace_id__threads__thread_id__inbox_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace_id: string;
        thread_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json":
          | components["schemas"]["Submission"]
          | components["schemas"]["FeedbackSubmission"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Submitted"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_runs_api_v1_workspaces__workspace_id__threads__thread_id__runs_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        workspace_id: string;
        thread_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RunPage"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  upload_api_v1_workspaces__workspace_id__uploads_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        workspace_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "multipart/form-data": components["schemas"]["Body_upload_api_v1_workspaces__workspace_id__uploads_post"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["UploadView"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_usage_api_v1_workspaces__workspace_id__usage_get: {
    parameters: {
      query: {
        run_id: string;
      };
      header?: never;
      path: {
        workspace_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["UsageView"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  health_healthz_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": {
            [key: string]: string;
          };
        };
      };
    };
  };
  ready_readyz_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": unknown;
        };
      };
    };
  };
}

export type Binary = Blob | ReadableStream<Uint8Array>;
