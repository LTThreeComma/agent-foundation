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
      /** Environment Template Id */
      environment_template_id?: string | null;
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
    /** AssetCreate */
    AssetCreate: {
      /** Name */
      name: string;
      /** Upload Id */
      upload_id: string;
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
    /** Body_upload_api_v1_workspaces__workspace_id__uploads_post */
    Body_upload_api_v1_workspaces__workspace_id__uploads_post: {
      /** File */
      file: string;
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
    /** ConnectionSelection */
    ConnectionSelection: {
      /** Connection Id */
      connection_id: string;
      /** Tools */
      tools: string[];
    };
    /**
     * CredentialMode
     * @enum {string}
     */
    CredentialMode: "required" | "optional" | "forbidden";
    /** HTTPValidationError */
    HTTPValidationError: {
      /** Detail */
      detail?: components["schemas"]["ValidationError"][];
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
    /** SessionProfile */
    SessionProfile: {
      /** Csrf Token */
      csrf_token: string;
      user: components["schemas"]["Profile"];
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
