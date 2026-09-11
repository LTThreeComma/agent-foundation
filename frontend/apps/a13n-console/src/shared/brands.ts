/** Display identities only. These mappings never select an endpoint or account. */
export interface Brand {
  icon: string;
  darkIcon?: string;
  invertInDark?: boolean;
  aliases?: readonly string[];
  hosts?: readonly string[];
}
const svgl = "https://svgl.app/library/";
const lobe =
  "https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@1.94.0/icons/";
export const brands: Record<string, Brand> = {
  github: {
    icon: `${svgl}github_light.svg`,
    darkIcon: `${svgl}github_dark.svg`,
    hosts: ["github.com", "api.githubcopilot.com"],
  },
  notion: {
    icon: `${svgl}notion.svg`,
    invertInDark: true,
    hosts: ["mcp.notion.com", "notion.so", "notion.com"],
  },
  linear: {
    icon: `${svgl}linear.svg`,
    hosts: ["mcp.linear.app", "linear.app"],
  },
  slack: { icon: `${svgl}slack.svg`, hosts: ["mcp.slack.com", "slack.com"] },
  gmail: { icon: `${svgl}gmail.svg`, hosts: ["mail.google.com"] },
  googlecalendar: {
    icon: `${svgl}google-calendar.svg`,
    aliases: ["google_calendar", "google-calendar"],
    hosts: ["calendar.google.com"],
  },
  googlesheets: {
    icon: `${svgl}google-sheets.svg`,
    aliases: ["google_sheets", "google-sheets"],
  },
  googledrive: {
    icon: "https://cdn.simpleicons.org/googledrive",
    aliases: ["google_drive", "google-drive"],
    hosts: ["drive.google.com"],
  },
  asana: {
    icon: `${svgl}asana-logo.svg`,
    hosts: ["mcp.asana.com", "app.asana.com"],
  },
  atlassian: {
    icon: `${svgl}atlassian.svg`,
    aliases: ["jira", "confluence"],
    hosts: ["mcp.atlassian.com"],
  },
  stripe: {
    icon: `${svgl}stripe.svg`,
    hosts: ["mcp.stripe.com", "stripe.com"],
  },
  sentry: {
    icon: `${svgl}sentry.svg`,
    invertInDark: true,
    hosts: ["mcp.sentry.dev", "sentry.io"],
  },
  vercel: {
    icon: `${svgl}vercel.svg`,
    darkIcon: `${svgl}vercel_dark.svg`,
    hosts: ["mcp.vercel.com", "vercel.com"],
  },
  hubspot: {
    icon: "https://cdn.simpleicons.org/hubspot",
    hosts: ["mcp.hubspot.com"],
  },
  dropbox: {
    icon: "https://cdn.simpleicons.org/dropbox",
    hosts: ["mcp.dropbox.com", "dropbox.com"],
  },
  context7: {
    icon: "https://cdn.jsdelivr.net/gh/upstash/context7@master/public/context7-icon-green.svg",
    hosts: ["mcp.context7.com", "context7.com"],
  },
  deepwiki: {
    icon: "https://deepwiki.com/icon.png",
    hosts: ["mcp.deepwiki.com", "deepwiki.com"],
  },
  composio: {
    icon: "https://composio.dev/logos/composio-black.svg",
    invertInDark: true,
  },
  brave: { icon: `${lobe}brave-color.svg` },
  exa: { icon: `${lobe}exa-color.svg` },
  openai: { icon: `${lobe}openai.svg`, invertInDark: true },
  anthropic: { icon: `${lobe}anthropic.svg`, invertInDark: true },
  google_gemini: { icon: `${lobe}gemini-color.svg` },
  google_vertex: { icon: `${lobe}vertexai-color.svg` },
  azure_openai: { icon: `${lobe}azure-color.svg` },
  aws_bedrock: { icon: `${lobe}bedrock-color.svg` },
  openrouter: { icon: `${lobe}openrouter-color.svg` },
  ollama: { icon: `${lobe}ollama.svg`, invertInDark: true },
  alibaba_model_studio: { icon: `${lobe}qwen-color.svg` },
  deepseek: { icon: `${lobe}deepseek-color.svg` },
  moonshot: { icon: `${lobe}kimi-color.svg` },
  zhipu: { icon: `${lobe}zhipu-color.svg` },
  "a13n.docker": {
    icon: "https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/docker/docker-original.svg",
  },
  "a13n.e2b": { icon: "https://e2b.dev/brand/e2b-symbol-fire-orange-s.svg" },
};

export function resolveBrand({
  identity,
  alias,
  endpoint,
}: {
  identity?: string;
  alias?: string;
  endpoint?: string;
}): Brand | undefined {
  if (identity && brands[identity]) return brands[identity];
  const normalized = alias?.trim().toLowerCase();
  if (normalized) {
    if (brands[normalized]) return brands[normalized];
    const matched = Object.values(brands).find((brand) =>
      brand.aliases?.includes(normalized),
    );
    if (matched) return matched;
  }
  if (endpoint) {
    try {
      const hostname = new URL(endpoint).hostname.toLowerCase();
      return Object.values(brands).find((brand) =>
        brand.hosts?.includes(hostname),
      );
    } catch {
      /* A custom URL can be incomplete while it is being edited. */
    }
  }
  return undefined;
}
