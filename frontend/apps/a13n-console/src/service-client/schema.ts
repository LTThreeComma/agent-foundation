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
  "/api/v1/organizations/{organization_id}/model-providers": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
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
    get?: never;
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
    get?: never;
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
    get?: never;
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
  "/api/v1/workspaces/{workspace_id}/threads": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Create Thread */
    post: operations["create_thread_api_v1_workspaces__workspace_id__threads_post"];
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
      /** Compaction Trigger Tokens */
      compaction_trigger_tokens?: number | null;
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
      payload: components["schemas"]["MessagePayload"] | null;
      /** Position */
      position: number;
      /**
       * Status
       * @enum {string}
       */
      status: "pending" | "assigned" | "consumed" | "failed" | "withdrawn";
      /** Thread Id */
      thread_id: string;
    };
    /** HTTPValidationError */
    HTTPValidationError: {
      /** Detail */
      detail?: components["schemas"]["ValidationError"][];
    };
    /** InboxPage */
    InboxPage: {
      /** Items */
      items: components["schemas"]["EntryView"][];
      /** Next Cursor */
      next_cursor: string | null;
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
    /** MessagePayload */
    MessagePayload: {
      /** Content */
      content: components["schemas"]["TextInput"][];
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
      /** Run Id */
      run_id: string;
      /** Segments */
      segments: components["schemas"]["Segment"][];
      status: components["schemas"]["RunStatus"];
    };
    /** RunOptions */
    RunOptions: {
      /** Labels */
      labels?: {
        [key: string]: string;
      };
      max_usage?: components["schemas"]["UsageLimit"] | null;
    };
    /** @enum {string} */
    RunStatus:
      "accepted" | "running" | "waiting" | "completed" | "failed" | "cancelled";
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
      /** Sealed At */
      sealed_at: string | null;
      /** Session Id */
      session_id: string;
      /** Source Entry Id */
      source_entry_id: string;
      status: components["schemas"]["RunStatus"];
      /** Thread Id */
      thread_id: string;
      /** Version */
      version: number;
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
       * Kind
       * @constant
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
       * Type
       * @constant
       */
      type: "text";
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
      /** Version */
      version: number;
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
        "application/json": components["schemas"]["Submission"];
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
